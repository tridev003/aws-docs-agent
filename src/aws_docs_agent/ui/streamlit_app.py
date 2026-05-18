"""Streamlit UI. Thin wrapper around AgentSession + Streamlit's chat widgets."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make `src/` importable when launched via `streamlit run path/to/file.py`.
SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import streamlit as st

from aws_docs_agent.agent.graph import AgentSession, TraceStep

# Maps trace event kinds to (icon, fallback Streamlit color label).
TRACE_ICONS: dict[str, str] = {
    "plan": "🧠",
    "tool_call": "🔎",
    "tool_result": "📄",
    "compose": "✍️",
    "warn": "⚠️",
}

logging.basicConfig(level=logging.WARNING)

# Show at most this many citation links per response. Beyond ~5 the panel
# becomes noise; we already dedupe by URL and rank by score upstream.
MAX_SOURCES_RENDERED = 5

st.set_page_config(
    page_title="AWS Docs Copilot",
    page_icon="📘",
    layout="centered",
    initial_sidebar_state="expanded",
)


# ----- session bootstrap ---------------------------------------------------


def _get_session() -> AgentSession:
    if "session" not in st.session_state:
        with st.spinner("Loading vector index and Bedrock client…"):
            st.session_state.session = AgentSession()
            st.session_state.turn_log = []
    return st.session_state.session


def _reset_session() -> None:
    st.session_state.pop("session", None)
    st.session_state.pop("turn_log", None)


def _render_sources(sources) -> None:
    """Sources panel: compact, deduped, capped at MAX_SOURCES_RENDERED."""
    if not sources:
        return
    top = sources[:MAX_SOURCES_RENDERED]
    bullets = "\n".join(f"- [{src.title}]({src.url})" for src in top)
    st.markdown(f"**📚 Sources**\n\n{bullets}")


# ----- sidebar -------------------------------------------------------------

with st.sidebar:
    st.markdown("### AWS Docs Copilot")
    st.caption("Agentic RAG over official AWS user guides, Bedrock + LangGraph.")

    session = _get_session()
    if not session.is_ready:
        st.error(
            "Vector index not found.\n\n"
            "Run `make ingest` (or `python -m aws_docs_agent.rag.ingest`) "
            "and refresh this page."
        )

    st.divider()
    st.markdown("**Settings**")
    st.code(
        f"region: {os.environ.get('AWS_REGION', 'us-east-1')}\n"
        f"chat:   {os.environ.get('BEDROCK_CHAT_MODEL_ID', '(default)')}\n"
        f"embed:  {os.environ.get('BEDROCK_EMBED_MODEL_ID', '(default)')}",
        language="text",
    )
    if st.button("🗑️  Reset conversation"):
        _reset_session()
        st.rerun()

    st.divider()
    st.markdown(
        "**Try asking**\n"
        "- How do I enable S3 versioning with the CLI?\n"
        "- What's the difference between IAM users and roles?\n"
        "- How does DynamoDB handle strongly consistent reads?\n"
        "- How do I configure automated RDS backups?"
    )


# ----- main chat area ------------------------------------------------------

st.title("📘 AWS Docs Copilot")
st.caption("Ask anything about AWS, answers come from the official documentation.")

session = _get_session()

# Replay history.
for entry in st.session_state.turn_log:
    with st.chat_message(entry["role"]):
        st.markdown(entry["content"])
        if entry.get("sources"):
            _render_sources(entry["sources"])


# Input box.
if user_input := st.chat_input(
    "Ask about AWS…" if session.is_ready else "Index not loaded, run `make ingest`",
    disabled=not session.is_ready,
):
    st.session_state.turn_log.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    answer, sources = "", []
    with st.chat_message("assistant"):
        with st.status("🤔 Thinking…", expanded=True) as status:
            try:
                for event in session.stream_turn(user_input):
                    if isinstance(event, TraceStep):
                        icon = TRACE_ICONS.get(event.kind, "•")
                        line = f"{icon} **{event.label}**"
                        if event.detail:
                            line += f", `{event.detail}`"
                        st.markdown(line)
                        status.update(label=f"{icon} {event.label}")
                    else:  # final-result dict
                        answer = event["final_text"]
                        sources = event["sources"]
                status.update(label="✅ Done", state="complete", expanded=False)
            except Exception as e:
                status.update(label=f"Error: {e}", state="error")
                st.error(str(e))
                raise
        st.markdown(answer or "_(no response)_")

        if sources:
            _render_sources(sources)

    st.session_state.turn_log.append(
        {"role": "assistant", "content": answer, "sources": sources}
    )
