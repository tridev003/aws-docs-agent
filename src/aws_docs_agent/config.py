"""Runtime config. Read env vars / .env once, hand out Settings everywhere else."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AWS / Bedrock
    aws_region: str = Field("us-east-1", alias="AWS_REGION")
    aws_profile: str | None = Field(None, alias="AWS_PROFILE")
    bedrock_chat_model_id: str = Field(
        # Bare claude-4.x IDs reject on-demand throughput; use the us.* inference profile.
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        alias="BEDROCK_CHAT_MODEL_ID",
    )
    bedrock_embed_model_id: str = Field(
        "amazon.titan-embed-text-v2:0", alias="BEDROCK_EMBED_MODEL_ID"
    )

    # Vector index
    index_local_path: Path = Field(
        REPO_ROOT / "data" / "index", alias="INDEX_LOCAL_PATH"
    )
    index_s3_bucket: str | None = Field(None, alias="INDEX_S3_BUCKET")
    index_s3_prefix: str = Field("faiss/", alias="INDEX_S3_PREFIX")

    # Ingestion
    ingest_sources_file: Path = Field(
        REPO_ROOT / "config" / "sources.yaml", alias="INGEST_SOURCES_FILE"
    )
    ingest_chunk_tokens: int = Field(750, alias="INGEST_CHUNK_TOKENS")
    ingest_chunk_overlap_tokens: int = Field(80, alias="INGEST_CHUNK_OVERLAP_TOKENS")
    ingest_max_files_per_repo: int = Field(400, alias="INGEST_MAX_FILES_PER_REPO")

    # Agent
    agent_max_tool_calls: int = Field(10, alias="AGENT_MAX_TOOL_CALLS")
    agent_top_k: int = Field(6, alias="AGENT_TOP_K")
    agent_temperature: float = Field(0.1, alias="AGENT_TEMPERATURE")

    # Tracing (optional)
    langsmith_api_key: str | None = Field(None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field("aws-docs-agent", alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(False, alias="LANGSMITH_TRACING")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
