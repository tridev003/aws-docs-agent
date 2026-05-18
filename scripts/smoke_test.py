#!/usr/bin/env python3
"""Quick non-interactive end-to-end smoke test.

Runs a small set of canned queries through the agent and prints whether each
came back with a non-empty answer and at least one source. Useful for CI in a
post-deploy job, or manually after a fresh `make ingest`.

Usage:
    python scripts/smoke_test.py
"""
from __future__ import annotations

import sys

from aws_docs_agent.agent.graph import AgentSession

PROMPTS = [
    "How do I enable versioning on an existing S3 bucket using the AWS CLI?",
    "What IAM permissions does a Lambda function need to write logs to CloudWatch?",
    "Which Bedrock embedding models are available?",
]


def main() -> int:
    # Each prompt gets a fresh session, we want to test independent queries,
    # not the agent's ability to handle conversational context.
    failures = 0
    for q in PROMPTS:
        print(f"\n--- {q}")
        session = AgentSession()
        if not session.is_ready:
            print("ERROR: vector index not built. Run `make ingest` first.", file=sys.stderr)
            return 2
        answer, sources, _ = session.turn(q)
        if not answer.strip():
            print("FAIL: empty answer")
            failures += 1
            continue
        if not sources:
            print("FAIL: no sources cited")
            failures += 1
            continue
        print(f"OK: {len(sources)} sources")
        print(f"     {answer[:240].replace(chr(10), ' ')}…")

    print(f"\n{len(PROMPTS) - failures}/{len(PROMPTS)} prompts passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
