"""FAISS retriever, persisted as 3 files on disk (or in S3).

IndexFlatIP over L2-normalized vectors = cosine similarity. Fine for the
corpus size here; swap for IVF/HNSW if it grows past ~100k chunks.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import boto3
import faiss
import numpy as np

from aws_docs_agent.bedrock.client import TitanEmbedder
from aws_docs_agent.config import get_settings

logger = logging.getLogger(__name__)

INDEX_FILENAME = "faiss.index"
META_FILENAME = "metadata.jsonl"
MANIFEST_FILENAME = "manifest.json"


@dataclass
class RetrievedChunk:
    score: float
    text: str
    metadata: dict

    @property
    def citation(self) -> str:
        md = self.metadata
        section = md.get("section_path") or md.get("display_name")
        return f"{md.get('display_name', 'AWS Docs')}: {section}"


class VectorIndex:
    """Build, persist, query. On disk: faiss.index + metadata.jsonl + manifest.json."""

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions
        self._index: faiss.Index | None = None
        self._metadata: list[dict] = []
        self._texts: list[str] = []

    # ----- build path --------------------------------------------------

    def add(self, *, embeddings: list[list[float]], texts: list[str], metadatas: list[dict]) -> None:
        if not (len(embeddings) == len(texts) == len(metadatas)):
            raise ValueError("embeddings, texts, metadatas must be the same length")
        if not embeddings:
            return
        vectors = np.asarray(embeddings, dtype="float32")
        if vectors.shape[1] != self.dimensions:
            raise ValueError(
                f"embedding dim {vectors.shape[1]} does not match index dim {self.dimensions}"
            )
        if self._index is None:
            self._index = faiss.IndexFlatIP(self.dimensions)
        self._index.add(vectors)
        self._texts.extend(texts)
        self._metadata.extend(metadatas)

    # ----- query path --------------------------------------------------

    def search(self, query_vector: list[float], k: int = 6) -> list[RetrievedChunk]:
        if self._index is None or self._index.ntotal == 0:
            return []
        q = np.asarray([query_vector], dtype="float32")
        scores, indices = self._index.search(q, k)
        results: list[RetrievedChunk] = []
        for score, idx in zip(scores[0], indices[0], strict=False):
            if idx < 0:
                continue
            results.append(
                RetrievedChunk(
                    score=float(score),
                    text=self._texts[idx],
                    metadata=self._metadata[idx],
                )
            )
        return results

    @property
    def size(self) -> int:
        return self._index.ntotal if self._index is not None else 0

    # ----- persistence -------------------------------------------------

    def save(self, path: Path, *, manifest: dict | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if self._index is None:
            raise RuntimeError("nothing to save: index is empty")
        faiss.write_index(self._index, str(path / INDEX_FILENAME))
        with (path / META_FILENAME).open("w", encoding="utf-8") as f:
            for text, meta in zip(self._texts, self._metadata, strict=True):
                f.write(json.dumps({"text": text, "metadata": meta}) + "\n")
        manifest_data = {
            "dimensions": self.dimensions,
            "size": self.size,
            **(manifest or {}),
        }
        (path / MANIFEST_FILENAME).write_text(json.dumps(manifest_data, indent=2))
        logger.info("Wrote FAISS index (%d vectors) to %s", self.size, path)

    @classmethod
    def load(cls, path: Path) -> VectorIndex:
        manifest = json.loads((path / MANIFEST_FILENAME).read_text())
        idx = cls(dimensions=manifest["dimensions"])
        idx._index = faiss.read_index(str(path / INDEX_FILENAME))
        with (path / META_FILENAME).open("r", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                idx._texts.append(row["text"])
                idx._metadata.append(row["metadata"])
        logger.info("Loaded FAISS index (%d vectors) from %s", idx.size, path)
        return idx

    # ----- S3 sync -----------------------------------------------------

    def upload_to_s3(self, bucket: str, prefix: str, local_path: Path) -> None:
        s3 = boto3.client("s3", region_name=get_settings().aws_region)
        for filename in (INDEX_FILENAME, META_FILENAME, MANIFEST_FILENAME):
            key = prefix.rstrip("/") + "/" + filename
            s3.upload_file(str(local_path / filename), bucket, key)
            logger.info("Uploaded s3://%s/%s", bucket, key)

    @classmethod
    def download_from_s3(cls, bucket: str, prefix: str, local_path: Path) -> Path:
        s3 = boto3.client("s3", region_name=get_settings().aws_region)
        local_path.mkdir(parents=True, exist_ok=True)
        for filename in (INDEX_FILENAME, META_FILENAME, MANIFEST_FILENAME):
            key = prefix.rstrip("/") + "/" + filename
            s3.download_file(bucket, key, str(local_path / filename))
            logger.info("Downloaded s3://%s/%s", bucket, key)
        return local_path


class Retriever:
    """Query interface for the agent. Lazy load + optional service filter."""

    def __init__(self, index: VectorIndex | None = None) -> None:
        self._index = index
        self._embedder: TitanEmbedder | None = None

    @classmethod
    def from_settings(cls) -> Retriever:
        s = get_settings()
        local = Path(s.index_local_path)
        manifest = local / MANIFEST_FILENAME
        if not manifest.exists() and s.index_s3_bucket:
            logger.info(
                "Local index missing; hydrating from s3://%s/%s",
                s.index_s3_bucket,
                s.index_s3_prefix,
            )
            VectorIndex.download_from_s3(s.index_s3_bucket, s.index_s3_prefix, local)
        if not manifest.exists():
            return cls(index=None)
        return cls(index=VectorIndex.load(local))

    @property
    def is_ready(self) -> bool:
        return self._index is not None and self._index.size > 0

    def _ensure_embedder(self) -> TitanEmbedder:
        if self._embedder is None:
            self._embedder = TitanEmbedder()
        return self._embedder

    def search(
        self,
        query: str,
        *,
        k: int = 6,
        service_filter: str | None = None,
    ) -> list[RetrievedChunk]:
        if not self.is_ready or not query.strip():
            return []
        vec = self._ensure_embedder().embed_one(query)
        # Over-fetch when filtering so we still get k after the service filter.
        oversample = k * 4 if service_filter else k
        hits = self._index.search(vec, k=oversample)  # type: ignore[union-attr]
        if service_filter:
            hits = [h for h in hits if h.metadata.get("service") == service_filter]
        return hits[:k]
