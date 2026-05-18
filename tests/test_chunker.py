"""Chunker tests, pure-Python, no AWS calls."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from aws_docs_agent.rag.chunker import (
    Chunk,
    _split_sections,
    chunk_markdown_file,
    count_tokens,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(dedent(body).strip() + "\n", encoding="utf-8")
    return p


def test_count_tokens_nonzero() -> None:
    assert count_tokens("hello world") >= 2


def test_split_sections_respects_headers() -> None:
    md = dedent(
        """\
        # Top
        intro

        ## Section A
        alpha

        ## Section B
        bravo

        ### Nested
        nested body
        """
    )
    sections = _split_sections(md)
    assert [s.headers for s in sections] == [
        ["Top"],
        ["Top", "Section A"],
        ["Top", "Section B"],
        ["Top", "Section B", "Nested"],
    ]


def test_split_sections_ignores_headers_in_code_fences() -> None:
    md = dedent(
        """\
        # Real

        ```
        # not a header
        more code
        ```

        ## Real subsection
        body
        """
    )
    sections = _split_sections(md)
    headers = [s.headers for s in sections]
    assert headers == [["Real"], ["Real", "Real subsection"]]


def test_chunk_markdown_file_smoke(tmp_path: Path) -> None:
    md = dedent(
        """\
        # Working with buckets

        Use Amazon S3 buckets to store objects.

        ## Naming rules

        Bucket names must be globally unique. They must be between 3 and 63
        characters long and follow DNS naming conventions.

        ## Versioning

        Versioning helps you recover from unintended deletes.
        """
    )
    path = _write(tmp_path, "using-buckets.md", md)
    chunks = chunk_markdown_file(
        path=path,
        service="s3",
        display_name="Amazon S3",
        base_doc_url="https://docs.aws.amazon.com/AmazonS3/latest/userguide/",
        max_tokens=2048,
    )
    # One chunk per section in this small file.
    assert len(chunks) == 3
    assert all(isinstance(c, Chunk) for c in chunks)
    assert chunks[0].source_url.endswith("/using-buckets.html")
    assert chunks[1].section_path == ["Working with buckets", "Naming rules"]
    assert chunks[1].service == "s3"
    assert chunks[1].display_name == "Amazon S3"


def test_chunk_markdown_splits_oversized_section(tmp_path: Path) -> None:
    # Build a single section large enough to require window splitting.
    long_body = "\n\n".join(["This is a paragraph about S3."] * 600)
    md = f"# Big\n\n{long_body}\n"
    path = _write(tmp_path, "big.md", md)
    chunks = chunk_markdown_file(
        path=path,
        service="s3",
        display_name="Amazon S3",
        base_doc_url="https://docs.aws.amazon.com/AmazonS3/latest/userguide/",
        max_tokens=200,
        overlap_tokens=20,
    )
    assert len(chunks) > 1
    assert all(c.token_count <= 220 for c in chunks)  # allow small encoder slop
    assert all(c.section_path == ["Big"] for c in chunks)


def test_chunk_markdown_strips_md_links(tmp_path: Path) -> None:
    md = "# Heading\n\nSee [the docs](other-page.md) for more info.\n"
    path = _write(tmp_path, "page.md", md)
    chunks = chunk_markdown_file(
        path=path,
        service="s3",
        display_name="Amazon S3",
        base_doc_url="https://docs.aws.amazon.com/AmazonS3/latest/userguide/",
    )
    assert len(chunks) == 1
    assert "the docs" in chunks[0].text
    assert "(other-page.md)" not in chunks[0].text


def test_to_metadata_shape() -> None:
    c = Chunk(
        service="s3",
        display_name="Amazon S3",
        source_path="/tmp/foo.md",
        source_url="https://docs.aws.amazon.com/.../foo.html",
        section_path=["A", "B"],
        text="body",
        token_count=4,
        chunk_index=2,
    )
    md = c.to_metadata()
    assert md["service"] == "s3"
    assert md["section_path"] == "A > B"
    assert md["chunk_index"] == 2
