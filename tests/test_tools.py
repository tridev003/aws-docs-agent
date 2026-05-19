"""Tools tests, exercise tool plumbing without invoking Bedrock or the network."""
from __future__ import annotations

from typing import Any

from aws_docs_agent.agent.tools import ToolKit, _fetch_aws_doc
from aws_docs_agent.rag.retriever import RetrievedChunk


class _StubIndex:
    """Minimal stand-in for VectorIndex; ToolKit only reads _metadata."""

    def __init__(self) -> None:
        self.size = 2
        self._metadata = [
            {"service": "s3", "display_name": "Amazon S3", "section_path": "X > Y", "source_url": "u"},
            {"service": "lambda", "display_name": "Lambda", "section_path": "A", "source_url": "v"},
        ]


class _StubRetriever:
    def __init__(self) -> None:
        self._index = _StubIndex()
        self._hits: list[RetrievedChunk] = []

    def queue(self, hits: list[RetrievedChunk]) -> None:
        self._hits = hits

    def search(self, query: str, *, k: int = 6, service_filter: str | None = None) -> list[RetrievedChunk]:
        if service_filter:
            return [
                h for h in self._hits if h.metadata.get("service") == service_filter
            ][:k]
        return self._hits[:k]


def _chunk(service: str, text: str = "body") -> RetrievedChunk:
    return RetrievedChunk(
        score=0.42,
        text=text,
        metadata={
            "service": service,
            "display_name": service.upper(),
            "section_path": "Section",
            "source_url": f"https://docs.aws.amazon.com/{service}/page.html",
        },
    )


def test_toolkit_indexed_services_from_metadata() -> None:
    kit = ToolKit(_StubRetriever())  # type: ignore[arg-type]
    assert kit.indexed_services() == ["lambda", "s3"]


def test_search_tool_records_sources() -> None:
    retriever = _StubRetriever()
    retriever.queue([_chunk("s3"), _chunk("lambda")])
    kit = ToolKit(retriever)  # type: ignore[arg-type]
    search_tool = next(t for t in kit.tools if t.name == "search_aws_docs")
    output = search_tool.func(query="how to enable versioning", k=2)
    assert "Amazon S3" in output or "S3" in output
    assert len(kit.sources) == 2
    assert kit.sources[0].service == "s3"


def test_search_tool_service_filter() -> None:
    retriever = _StubRetriever()
    retriever.queue([_chunk("s3"), _chunk("lambda"), _chunk("s3")])
    kit = ToolKit(retriever)  # type: ignore[arg-type]
    search_tool = next(t for t in kit.tools if t.name == "search_aws_docs")
    output = search_tool.func(query="anything", service_filter="lambda", k=5)
    assert "LAMBDA" in output
    assert all(s.service == "lambda" for s in kit.sources)


def test_fetch_tool_refuses_non_aws_hosts() -> None:
    out = _fetch_aws_doc("https://example.com/bad")
    assert out.startswith("Refused")


def test_list_indexed_services_when_empty() -> None:
    class _EmptyRetriever:
        _index = None

        def search(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
            return []

    kit = ToolKit(_EmptyRetriever())  # type: ignore[arg-type]
    list_tool = next(t for t in kit.tools if t.name == "list_indexed_services")
    out = list_tool.func()
    assert "empty" in out.lower()
