"""Ingestion: clone awsdocs repos, chunk, embed, write a FAISS index.

Run via `python -m aws_docs_agent.rag.ingest` or `make ingest`. Builds into
a temp dir and atomically swaps the result in so a Ctrl-C doesn't leave a
half-written index behind.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import yaml
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from aws_docs_agent.bedrock.client import TitanEmbedder
from aws_docs_agent.config import get_settings
from aws_docs_agent.rag.chunker import Chunk, chunk_markdown_file
from aws_docs_agent.rag.retriever import VectorIndex

logger = logging.getLogger(__name__)
console = Console()

EMBED_BATCH = 16  # Bedrock Titan is single-doc; this controls thread fan-out.


@dataclass
class Source:
    service: str
    display_name: str
    repo: str
    subpath: str
    base_doc_url: str
    branch: str | None = None


def _load_sources(path: Path) -> list[Source]:
    raw = yaml.safe_load(path.read_text())
    return [Source(**entry) for entry in raw.get("sources", [])]


def _clone(repo: str, target: Path, *, branch: str | None = None) -> None:
    """Shallow git clone. Branch is configurable per source.

    AWS rotated several awsdocs default branches to `archived` (empty),
    with the real content still on `main`. Hence the explicit branch knob.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repo, str(target)]
    subprocess.run(cmd, check=True, capture_output=True)


def _collect_chunks(source: Source, repo_dir: Path, *, max_files: int) -> list[Chunk]:
    docs_dir = repo_dir / source.subpath
    if not docs_dir.exists():
        logger.warning("Subpath %s missing in %s", source.subpath, repo_dir)
        return []
    md_files = sorted(docs_dir.glob("*.md"))[:max_files]
    settings = get_settings()
    chunks: list[Chunk] = []
    for md in md_files:
        chunks.extend(
            chunk_markdown_file(
                path=md,
                service=source.service,
                display_name=source.display_name,
                base_doc_url=source.base_doc_url,
                max_tokens=settings.ingest_chunk_tokens,
                overlap_tokens=settings.ingest_chunk_overlap_tokens,
            )
        )
    return chunks


def _embed_chunks(chunks: list[Chunk], *, workers: int = 8) -> list[list[float]]:
    """Embed chunks via thread pool. Titan v2 is one-doc-per-call."""
    embedder = TitanEmbedder()
    embeddings: list[list[float] | None] = [None] * len(chunks)

    with Progress(
        TextColumn("[bold cyan]embedding"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("embed", total=len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(embedder.embed_one, c.text): i for i, c in enumerate(chunks)}
            for future in as_completed(futures):
                idx = futures[future]
                embeddings[idx] = future.result()
                progress.advance(task)

    return [e for e in embeddings if e is not None]  # type: ignore[misc]


def run_ingestion(*, sources_file: Path | None = None) -> Path:
    settings = get_settings()
    sources_file = sources_file or settings.ingest_sources_file
    sources = _load_sources(sources_file)
    console.rule(f"[bold]Ingesting {len(sources)} sources")

    work_root = Path(tempfile.mkdtemp(prefix="awsdocs-ingest-"))
    all_chunks: list[Chunk] = []
    try:
        for src in sources:
            console.print(f"[cyan]clone[/cyan]  {src.repo}")
            repo_dir = work_root / src.service
            t0 = time.time()
            _clone(src.repo, repo_dir, branch=src.branch)
            console.print(f"[green]cloned[/green] {src.service} in {time.time() - t0:.1f}s")

            chunks = _collect_chunks(src, repo_dir, max_files=settings.ingest_max_files_per_repo)
            console.print(f"[green]chunked[/green] {src.service}: {len(chunks)} chunks")
            all_chunks.extend(chunks)

        if not all_chunks:
            raise RuntimeError("Ingestion produced zero chunks, check sources.yaml")

        embeddings = _embed_chunks(all_chunks)

        staging = Path(tempfile.mkdtemp(prefix="awsdocs-index-"))
        index = VectorIndex(dimensions=len(embeddings[0]))
        index.add(
            embeddings=embeddings,
            texts=[c.text for c in all_chunks],
            metadatas=[c.to_metadata() for c in all_chunks],
        )
        index.save(
            staging,
            manifest={
                "embed_model": settings.bedrock_embed_model_id,
                "sources": [s.service for s in sources],
                "chunk_count": len(all_chunks),
            },
        )

        final = Path(settings.index_local_path)
        if final.exists():
            shutil.rmtree(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(final))
        console.print(f"[bold green]✓[/bold green] Index ready at {final}")

        if settings.index_s3_bucket:
            console.print(
                f"[cyan]uploading[/cyan] to s3://{settings.index_s3_bucket}/{settings.index_s3_prefix}"
            )
            index.upload_to_s3(settings.index_s3_bucket, settings.index_s3_prefix, final)

        return final
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Ingest AWS docs into the FAISS index")
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="Override the sources.yaml location",
    )
    args = parser.parse_args()
    run_ingestion(sources_file=args.sources)


if __name__ == "__main__":
    main()
