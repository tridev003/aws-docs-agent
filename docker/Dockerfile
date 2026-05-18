# syntax=docker/dockerfile:1.7

# ---- builder ----------------------------------------------------------------
# Compile heavy deps (faiss-cpu wheel, langchain) in a builder image so the
# runtime image carries no toolchain. Multi-arch friendly: targetarch is set
# by buildx when we cross-build linux/amd64 from an Apple Silicon mac.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -U pip \
    && /opt/venv/bin/pip install -r requirements.txt


# ---- runtime ----------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true

# git is required at runtime by the ingestion pipeline (shallow clone of
# awsdocs repos). curl is kept for the App Runner / ECS healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY config ./config
RUN /opt/venv/bin/pip install --no-deps -e .

# Index is mounted as a volume locally; in deployed environments it's
# downloaded from S3 at boot via INDEX_S3_BUCKET / INDEX_S3_PREFIX.
RUN mkdir -p /app/data/index && chown -R app:app /app

USER app
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/aws_docs_agent/ui/streamlit_app.py"]
