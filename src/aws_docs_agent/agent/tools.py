"""Agent tools: search, fetch, list_services.

Each one is a LangChain StructuredTool so LangGraph's ToolNode can run it.
Tools return strings for the LLM; structured Source records are also pushed
onto the ToolKit so the UI can render citations without re-parsing.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from aws_docs_agent.config import get_settings
from aws_docs_agent.rag.retriever import RetrievedChunk, Retriever

logger = logging.getLogger(__name__)

# Don't let the agent pull in megabytes of HTML and blow the context window.
_FETCH_MAX_BYTES = 200_000
_AWS_DOCS_HOST = "docs.aws.amazon.com"


class Source(BaseModel):
    """A retrieved chunk surfaced to the UI as a citation."""

    title: str
    service: str
    url: str
    score: float
    snippet: str


# --- Tool arg schemas (Pydantic so the LLM sees a JSON schema) ---


class SearchArgs(BaseModel):
    query: str = Field(..., description="Natural-language search query.")
    service_filter: str | None = Field(
        None,
        description=(
            "Optional service slug to scope the search "
            "(e.g. 's3', 'ec2', 'lambda', 'iam', 'bedrock'). Omit for cross-service search."
        ),
    )
    k: int = Field(6, ge=1, le=12, description="Number of chunks to return.")


class FetchArgs(BaseModel):
    url: str = Field(
        ...,
        description="Fully-qualified docs.aws.amazon.com URL to fetch. Other hosts are refused.",
    )


class ListArgs(BaseModel):
    pass


# --- Tool implementations ---


def _format_chunks(chunks: Sequence[RetrievedChunk]) -> str:
    """Format retrieval hits as a numbered transcript for the model."""
    if not chunks:
        return "No results found in the indexed documentation."
    blocks = []
    for i, c in enumerate(chunks, start=1):
        md = c.metadata
        blocks.append(
            f"[{i}] {md.get('display_name')}, {md.get('section_path')}\n"
            f"URL: {md.get('source_url')}\n"
            f"Score: {c.score:.3f}\n"
            f"---\n{c.text}\n"
        )
    return "\n".join(blocks)


def _fetch_aws_doc(url: str) -> str:
    """Fetch one docs.aws.amazon.com page. Refuses any other host."""
    if _AWS_DOCS_HOST not in url:
        return f"Refused: the fetch tool only accepts URLs on {_AWS_DOCS_HOST}."
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            response = client.get(
                url,
                headers={"User-Agent": "aws-docs-agent/0.1 (educational)"},
            )
            response.raise_for_status()
            text = response.text[:_FETCH_MAX_BYTES]
    except httpx.HTTPError as e:
        return f"Fetch failed: {e}"

    # Crude tag stripping. A real extractor (readability/trafilatura) would
    # be nicer but this is the fallback path, not the main one. TODO maybe.
    import re

    text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


class ToolKit:
    """Tools + a per-turn list of structured search hits the UI reads back."""

    def __init__(self, retriever: Retriever) -> None:
        self.retriever = retriever
        self.sources: list[Source] = []
        self._tools = self._build_tools()

    @property
    def tools(self) -> list[StructuredTool]:
        return self._tools

    def reset_sources(self) -> None:
        self.sources = []

    def indexed_services(self) -> list[str]:
        # `_metadata` lives on VectorIndex; expose it through Retriever rather
        # than reaching in here.
        idx = self.retriever._index  # type: ignore[attr-defined]
        if idx is None:
            return []
        return sorted({m.get("service", "") for m in idx._metadata if m.get("service")})

    def _build_tools(self) -> list[StructuredTool]:
        def search(query: str, service_filter: str | None = None, k: int = 6) -> str:
            settings = get_settings()
            k = min(k, settings.agent_top_k * 2)
            hits = self.retriever.search(query, k=k, service_filter=service_filter)
            for h in hits:
                md = h.metadata
                self.sources.append(
                    Source(
                        title=f"{md.get('display_name')}, {md.get('section_path')}",
                        service=md.get("service", ""),
                        url=md.get("source_url", ""),
                        score=h.score,
                        snippet=h.text[:400],
                    )
                )
            return _format_chunks(hits)

        def fetch(url: str) -> str:
            return _fetch_aws_doc(url)

        def list_services() -> str:
            svcs = self.indexed_services()
            if not svcs:
                return "The vector index is empty. Run ingestion first."
            return "Indexed services: " + ", ".join(svcs)

        return [
            StructuredTool.from_function(
                func=search,
                name="search_aws_docs",
                description=(
                    "Semantic search over the indexed AWS documentation. "
                    "Use this first when answering AWS questions."
                ),
                args_schema=SearchArgs,
            ),
            StructuredTool.from_function(
                func=fetch,
                name="fetch_aws_doc_page",
                description=(
                    "Fetch a specific docs.aws.amazon.com URL. "
                    "Use this only when search misses or the user provides a URL."
                ),
                args_schema=FetchArgs,
            ),
            StructuredTool.from_function(
                func=list_services,
                name="list_indexed_services",
                description="List which AWS services are present in the local index.",
                args_schema=ListArgs,
            ),
        ]
