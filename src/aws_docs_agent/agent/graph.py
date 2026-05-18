"""LangGraph workflow: agent <-> tools loop, with a recursion_limit cap.

We can't inject a "stop" message between an AI tool_use and its tool_result
(Anthropic's Converse API rejects that), so we use LangGraph's
recursion_limit instead of a custom force-finalize node.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from aws_docs_agent.agent.prompts import render_system_prompt
from aws_docs_agent.agent.tools import Source, ToolKit
from aws_docs_agent.bedrock.client import make_chat_model
from aws_docs_agent.config import get_settings
from aws_docs_agent.rag.retriever import Retriever

logger = logging.getLogger(__name__)


@dataclass
class TraceStep:
    """One observable agent step, streamed to the UI as it happens."""

    kind: str  # plan | tool_call | tool_result | compose | warn
    label: str
    detail: str = ""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build_agent_graph(retriever: Retriever):
    """Compile the graph and hand back its toolkit (the UI reads citations off it)."""
    settings = get_settings()
    toolkit = ToolKit(retriever)
    llm = make_chat_model().bind_tools(toolkit.tools)
    tool_node = ToolNode(toolkit.tools)

    def agent_node(state: AgentState) -> dict[str, Any]:
        # Re-render the system prompt each call so the indexed-service list
        # and today's date are always current.
        sys_msg = SystemMessage(
            content=render_system_prompt(
                max_tool_calls=settings.agent_max_tool_calls,
                service_list=toolkit.indexed_services(),
                today=datetime.now(timezone.utc).date().isoformat(),
            )
        )
        history = [sys_msg, *state["messages"]]
        ai_msg = llm.invoke(history)
        return {"messages": [ai_msg]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return END

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    return workflow.compile(), toolkit


class AgentSession:
    """One conversation. Wraps the graph, keeps message history across turns."""

    def __init__(self) -> None:
        self.retriever = Retriever.from_settings()
        self.graph, self.toolkit = build_agent_graph(self.retriever)
        self.messages: list[AnyMessage] = []

    @property
    def is_ready(self) -> bool:
        return self.retriever.is_ready

    def turn(self, user_input: str) -> tuple[str, list[Source], list[AnyMessage]]:
        """Run one turn, no streaming. Used by the CLI / smoke tests."""
        final_text = ""
        sources: list[Source] = []
        new_messages: list[AnyMessage] = []
        for event in self.stream_turn(user_input):
            if isinstance(event, dict):
                final_text = event["final_text"]
                sources = event["sources"]
                new_messages = event["new_messages"]
        return final_text, sources, new_messages

    def stream_turn(self, user_input: str) -> Iterator[TraceStep | dict]:
        """Yield TraceStep events as the graph runs, then a final dict.

        Final yield: `{"final_text", "sources", "new_messages"}`. Used by the
        Streamlit UI to render the live "thinking" trace.
        """
        self.toolkit.reset_sources()
        prior_len = len(self.messages)
        self.messages.append(HumanMessage(content=user_input))
        state: AgentState = {"messages": self.messages}
        settings = get_settings()
        recursion_limit = 2 * settings.agent_max_tool_calls + 2

        new_history: list[AnyMessage] = list(self.messages)
        hit_limit = False

        yield TraceStep("plan", "Planning the answer", "deciding which tools to call")

        try:
            for chunk in self.graph.stream(
                state,
                config={"recursion_limit": recursion_limit},
                stream_mode="updates",
            ):
                for node_name, update in chunk.items():
                    msgs = update.get("messages", []) or []
                    new_history.extend(msgs)
                    yield from _trace_for_update(node_name, msgs)
        except GraphRecursionError:
            logger.warning("Recursion limit hit; returning partial conversation.")
            hit_limit = True
            yield TraceStep(
                "warn",
                "Tool-call budget exhausted",
                f"agent made {settings.agent_max_tool_calls}+ calls without finalizing",
            )

        self.messages = new_history
        new_this_turn = new_history[prior_len:]

        final_text = ""
        if not hit_limit:
            for msg in reversed(new_this_turn):
                if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                    final_text = _stringify(msg.content)
                    break
        if not final_text:
            final_text = (
                "I hit my tool-call budget before reaching a final answer. "
                "Try rephrasing more narrowly, or ask me to summarize what I found so far."
            )

        # Dedupe by URL: multiple retrievals often hit the same doc page.
        deduped: dict[str, Source] = {}
        for src in self.toolkit.sources:
            existing = deduped.get(src.url)
            if existing is None or src.score > existing.score:
                deduped[src.url] = src
        sources = sorted(deduped.values(), key=lambda s: s.score, reverse=True)

        yield {"final_text": final_text, "sources": sources, "new_messages": new_this_turn}


def _trace_for_update(node_name: str, msgs: list[AnyMessage]) -> Iterator[TraceStep]:
    """Translate a LangGraph node update into one or more UI-friendly trace steps."""
    if node_name == "agent":
        for m in msgs:
            if not isinstance(m, AIMessage):
                continue
            tool_calls = getattr(m, "tool_calls", None) or []
            if tool_calls:
                for tc in tool_calls:
                    yield _trace_for_tool_call(tc["name"], tc.get("args", {}) or {})
            else:
                yield TraceStep("compose", "Composing answer", "synthesizing retrieved context")
    elif node_name == "tools":
        for m in msgs:
            if not isinstance(m, ToolMessage):
                continue
            yield _trace_for_tool_result(m)


def _trace_for_tool_call(name: str, args: dict[str, Any]) -> TraceStep:
    if name == "search_aws_docs":
        query = str(args.get("query", "")).strip()
        svc = args.get("service_filter")
        scope = f"in {svc}" if svc else "across all indexed services"
        return TraceStep("tool_call", f"Searching docs ({scope})", query[:160])
    if name == "fetch_aws_doc_page":
        return TraceStep("tool_call", "Fetching doc page", str(args.get("url", "")))
    if name == "list_indexed_services":
        return TraceStep("tool_call", "Listing indexed services", "")
    return TraceStep("tool_call", f"Calling {name}", str(args)[:160])


def _trace_for_tool_result(msg: ToolMessage) -> TraceStep:
    name = msg.name or "tool"
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    if name == "search_aws_docs":
        hits = text.count("\n[")  # number of result blocks
        if "[1]" in text:
            hits += 1
        if hits == 0:
            return TraceStep("tool_result", "Search returned no results", "")
        return TraceStep("tool_result", f"Retrieved {hits} doc chunks", "")
    if name == "fetch_aws_doc_page":
        if text.startswith("Refused") or text.startswith("Fetch failed"):
            return TraceStep("warn", "Fetch rejected", text[:120])
        return TraceStep("tool_result", "Fetched doc page", f"{len(text)} chars extracted")
    if name == "list_indexed_services":
        return TraceStep("tool_result", "Got indexed-service list", text[:160])
    return TraceStep("tool_result", f"{name} returned", text[:120])


_TRAILING_SOURCES_RE = re.compile(
    r"\n\s*(?:#{1,6}\s*|\*\*)?Sources?\s*:?\s*\*{0,2}\s*\n.*$",
    re.IGNORECASE | re.DOTALL,
)


def _stringify(content: Any) -> str:
    """Normalize Converse content to a string. Strip any trailing 'Sources'
    block the model wrote despite the system prompt (UI renders sources itself).
    """
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        text = "".join(parts)
    else:
        text = str(content)
    return _TRAILING_SOURCES_RE.sub("", text).rstrip()
