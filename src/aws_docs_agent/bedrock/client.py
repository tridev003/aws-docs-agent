"""Bedrock wrappers: a chat-model factory and a Titan embedding helper.

Kept narrow so swapping models or adding retries/logging happens here, not
across the rest of the codebase.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from langchain_aws import ChatBedrockConverse
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from aws_docs_agent.config import get_settings

logger = logging.getLogger(__name__)


def _boto_session() -> boto3.Session:
    settings = get_settings()
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_profile:
        kwargs["profile_name"] = settings.aws_profile
    return boto3.Session(**kwargs)


def bedrock_runtime():
    """Return a configured bedrock-runtime boto3 client."""
    config = BotoConfig(
        retries={"max_attempts": 5, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=60,
    )
    return _boto_session().client("bedrock-runtime", config=config)


def make_chat_model(
    *,
    temperature: float | None = None,
    max_tokens: int = 2048,
    model_id: str | None = None,
):
    """LangChain chat model on top of the Bedrock Converse API."""
    settings = get_settings()
    return ChatBedrockConverse(
        model_id=model_id or settings.bedrock_chat_model_id,
        region_name=settings.aws_region,
        temperature=settings.agent_temperature if temperature is None else temperature,
        max_tokens=max_tokens,
    )


class TitanEmbedder:
    """One-doc-per-call embedder for Titan v2. No caching here, caller owns it."""

    def __init__(self, dimensions: int = 1024) -> None:
        self.client = bedrock_runtime()
        self.model_id = get_settings().bedrock_embed_model_id
        self.dimensions = dimensions

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, max=20),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def embed_one(self, text: str) -> list[float]:
        body = json.dumps(
            {
                "inputText": text,
                "dimensions": self.dimensions,
                "normalize": True,
            }
        )
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=body,
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        return payload["embedding"]

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]
