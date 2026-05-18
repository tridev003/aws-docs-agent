"""Markdown chunker. Split by header first, fall back to paragraph windows.

AWS docs are one markdown file per topic with stable H1/H2 structure, so
header splits produce self-contained chunks. Each chunk keeps a breadcrumb
section path so retrievals are interpretable in the UI.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

logger = logging.getLogger(__name__)

# Claude's tokenizer isn't public; cl100k_base is close enough for sizing.
# Overcounts a bit, which biases chunks slightly smaller. Fine.
_TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")

_FENCE_RE = re.compile(r"^```")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# Inline [text](file.md) links: keep the text, drop the relative path.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# awsdocs ships <a name="..."></a> next to headers and escapes punctuation
# like `\.` / `\(`. Both pollute embeddings and citation titles.
_AWS_ANCHOR_RE = re.compile(r'<a\s+name="[^"]*"\s*></a>')
_AWS_ESCAPE_RE = re.compile(r"\\([.()\-_*+<>\[\]{}!])")


def _strip_awsdocs_noise(text: str) -> str:
    text = _AWS_ANCHOR_RE.sub("", text)
    text = _AWS_ESCAPE_RE.sub(r"\1", text)
    return text


def count_tokens(text: str) -> int:
    return len(_TOKEN_ENCODER.encode(text))


@dataclass
class Chunk:
    """One retrievable doc chunk. `section_path` is the H1>H2>H3 breadcrumb."""

    service: str
    display_name: str
    source_path: str
    source_url: str
    section_path: list[str]
    text: str
    token_count: int = 0
    chunk_index: int = 0

    def to_metadata(self) -> dict:
        return {
            "service": self.service,
            "display_name": self.display_name,
            "source_path": self.source_path,
            "source_url": self.source_url,
            "section_path": " > ".join(self.section_path),
            "chunk_index": self.chunk_index,
        }


@dataclass
class _Section:
    headers: list[str]
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _split_sections(markdown: str) -> list[_Section]:
    """Emit one section per markdown header. Respects fenced code blocks."""
    sections: list[_Section] = []
    header_stack: list[str] = []
    current = _Section(headers=[])
    in_fence = False

    for raw_line in markdown.splitlines():
        if _FENCE_RE.match(raw_line):
            in_fence = not in_fence
            current.lines.append(raw_line)
            continue

        match = None if in_fence else _HEADER_RE.match(raw_line)
        if match:
            if current.lines:
                sections.append(current)
            level = len(match.group(1))
            title = _strip_awsdocs_noise(match.group(2)).strip()
            header_stack = header_stack[: level - 1] + [title]
            current = _Section(headers=list(header_stack))
        else:
            current.lines.append(raw_line)

    if current.lines:
        sections.append(current)

    return [s for s in sections if s.text]


def _clean(text: str) -> str:
    text = _strip_awsdocs_noise(text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_oversized(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split a too-long section into paragraph windows with overlap."""
    paragraphs = [p for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush() -> None:
        if current:
            chunks.append("\n\n".join(current))

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if para_tokens > max_tokens:
            flush()
            current, current_tokens = [], 0
            ids = _TOKEN_ENCODER.encode(para)
            step = max_tokens - overlap_tokens
            for start in range(0, len(ids), step):
                window = ids[start : start + max_tokens]
                chunks.append(_TOKEN_ENCODER.decode(window))
            continue

        if current_tokens + para_tokens > max_tokens and current:
            flush()
            tail = current[-1] if overlap_tokens > 0 else None
            current = [tail] if tail else []
            current_tokens = count_tokens(tail) if tail else 0

        current.append(para)
        current_tokens += para_tokens

    flush()
    return chunks


def chunk_markdown_file(
    *,
    path: Path,
    service: str,
    display_name: str,
    base_doc_url: str,
    max_tokens: int = 750,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    """Chunk a single markdown file from an awsdocs repo."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    sections = _split_sections(text)
    if not sections:
        return []

    # awsdocs foo.md -> foo.html on the public site.
    stem = path.stem
    source_url = base_doc_url.rstrip("/") + f"/{stem}.html"

    chunks: list[Chunk] = []
    for section in sections:
        body = _clean(section.text)
        if not body:
            continue
        headers = section.headers or [stem.replace("-", " ").title()]
        pieces = (
            [body]
            if count_tokens(body) <= max_tokens
            else _split_oversized(body, max_tokens, overlap_tokens)
        )
        for piece in pieces:
            chunks.append(
                Chunk(
                    service=service,
                    display_name=display_name,
                    source_path=str(path),
                    source_url=source_url,
                    section_path=headers,
                    text=piece,
                    token_count=count_tokens(piece),
                    chunk_index=len(chunks),
                )
            )

    return chunks
