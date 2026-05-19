"""Retriever tests, exercise FAISS round-trip without touching Bedrock."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from aws_docs_agent.rag.retriever import Retriever, VectorIndex


def _vec(seed: int, dim: int = 8) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype("float32")
    v /= np.linalg.norm(v)
    return v.tolist()


def test_build_and_query_roundtrip(tmp_path: Path) -> None:
    idx = VectorIndex(dimensions=8)
    embeddings = [_vec(i) for i in range(5)]
    texts = [f"doc {i}" for i in range(5)]
    metas = [{"service": "s3", "display_name": "Amazon S3", "chunk_index": i} for i in range(5)]
    idx.add(embeddings=embeddings, texts=texts, metadatas=metas)

    # Query with the first vector; it should be its own nearest neighbor.
    hits = idx.search(embeddings[0], k=3)
    assert len(hits) == 3
    assert hits[0].text == "doc 0"
    assert hits[0].score > 0.99

    idx.save(tmp_path, manifest={"embed_model": "fake"})
    reloaded = VectorIndex.load(tmp_path)
    assert reloaded.size == 5
    rehits = reloaded.search(embeddings[2], k=1)
    assert rehits[0].text == "doc 2"


def test_dimension_mismatch_raises(tmp_path: Path) -> None:
    idx = VectorIndex(dimensions=8)
    with pytest.raises(ValueError):
        idx.add(
            embeddings=[_vec(0, dim=16)],
            texts=["x"],
            metadatas=[{"service": "s3"}],
        )


def test_retriever_service_filter(monkeypatch) -> None:
    idx = VectorIndex(dimensions=8)
    embeddings = [_vec(i) for i in range(6)]
    services = ["s3", "s3", "ec2", "ec2", "lambda", "lambda"]
    idx.add(
        embeddings=embeddings,
        texts=[f"doc-{i}" for i in range(6)],
        metadatas=[{"service": s, "display_name": s.upper()} for s in services],
    )

    r = Retriever(index=idx)
    # Stub out the embedder so we don't call Bedrock.
    class _FakeEmbedder:
        def embed_one(self, _text: str) -> list[float]:
            return embeddings[0]

    r._embedder = _FakeEmbedder()  # type: ignore[assignment]

    only_ec2 = r.search("anything", k=4, service_filter="ec2")
    assert {h.metadata["service"] for h in only_ec2} == {"ec2"}
    assert len(only_ec2) == 2
