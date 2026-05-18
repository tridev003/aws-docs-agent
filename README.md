# aws-docs-agent

A chatbot that answers AWS questions by retrieving from the official AWS user
guides. The agent decides when to search, what to search for, and when it has
enough material to write an answer. Citations are shown alongside each
response so you can verify what the model claimed.

Built with LangGraph + Bedrock (Claude Sonnet 4.5 for chat, Titan v2 for
embeddings), FAISS for retrieval, Streamlit for the UI, and Terraform for the
AWS infra.

## Why

Take-home for an interview. Brief was: build an agentic chatbot over AWS
docs with RAG, with IaC for the deploy. I optimized the stack for "actually
works end-to-end on a free-ish AWS account":

- Bedrock for both chat and embeddings, so everything stays in one account.
- FAISS persisted to S3 (instead of OpenSearch Serverless, which costs ~$700/mo even idle).
- App Runner instead of ECS+ALB, so I don't have to wrangle a VPC.
- Streamlit because building a React frontend wasn't the point of the assignment.

This is not a production system. It's a working prototype. See
`docs/ARCHITECTURE.md` if you want my reasoning on each choice.

## Quick start (local)

You'll need Python 3.11+, an AWS account with Bedrock enabled, and `aws`
configured (`aws configure` or SSO, doesn't matter which).

```bash
git clone <this-repo>
cd aws-docs-agent

# Editable install. If you don't have python 3.11, `uv python install 3.11`
# is the fastest way to get there.
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Sanity check
pytest

# Build the FAISS index. Clones 5 awsdocs repos, embeds ~6k chunks via Titan,
# takes about 4-5 minutes. Costs ~$0.06.
make ingest

# Run the UI
make run
```

Open http://localhost:8501 and ask it something. Indexed services: S3, IAM,
DynamoDB, RDS, SQS. Anything outside that scope, the agent should say so
explicitly instead of guessing.

There's also a tiny CLI if you don't feel like opening the browser:

```bash
aws-docs-chat
```

## Deploy to AWS

Two-phase apply. App Runner refuses to create a service if the image isn't
already in ECR, so we build the ECR repo first, push, then turn the service
on.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit owner_email at minimum

terraform init
terraform apply                       # creates ECR + S3 + IAM

cd ..
make push-image                       # builds and pushes the container

INDEX_S3_BUCKET=$(terraform -chdir=infra output -raw index_bucket) make ingest

cd infra
terraform apply -var "create_app_runner=true"
terraform output app_runner_url       # public URL
```

To tear it down: `terraform destroy`. Do that, please. App Runner is
pay-per-second and I forgot to once.

### Estimated cost

App Runner is the dominant line item. Everything else rounds to zero at
demo traffic.

| Service          | Driver                       | Estimate |
| ---------------- | ---------------------------- | -------- |
| App Runner       | 1× 1vCPU/2GB, always-on, 30d | ~$60     |
| Bedrock (Claude) | ~50 turns, ~5k tok each      | ~$2      |
| Bedrock (Titan)  | One-off ingest               | <$0.10   |
| S3 + ECR         | ~50 MB                       | <$0.10   |

`app_runner_min_instances = 0` scales the service to zero between sessions
but the first message after idle takes ~30 seconds (FAISS reloads from S3).

## Configuration

Everything goes through `.env` (read by pydantic-settings). The interesting
knobs:

| Variable                 | Default                                          |
| ------------------------ | ------------------------------------------------ |
| `AWS_REGION`             | `us-east-1`                                      |
| `BEDROCK_CHAT_MODEL_ID`  | `us.anthropic.claude-sonnet-4-5-20250929-v1:0`   |
| `BEDROCK_EMBED_MODEL_ID` | `amazon.titan-embed-text-v2:0`                   |
| `INDEX_S3_BUCKET`        | unset (local-only); set to hydrate from S3       |
| `AGENT_MAX_TOOL_CALLS`   | 10                                               |
| `AGENT_TOP_K`            | 6                                                |

The `us.` prefix on the chat model is a cross-region inference profile,
which is what newer Anthropic models on Bedrock require (the bare model ID
errors out with "on-demand throughput isn't supported"). Took me a confused
half-hour to figure that out.

To add a service to the index, edit `config/sources.yaml` and re-run
ingestion. There's a `branch:` field on each source because AWS rotated
some of the awsdocs repos' default branches to `archived` (empty) while
the real content stayed on `main`. Another confused half-hour.

## What's in the box

```
src/aws_docs_agent/
  config.py             # pydantic-settings, the only place we read env vars
  bedrock/client.py     # boto3 + retries; isolates the Bedrock SDK
  rag/
    chunker.py          # markdown-aware section chunker
    retriever.py        # FAISS + optional S3 hydration
    ingest.py           # clone awsdocs, chunk, embed, save
  agent/
    prompts.py          # one system prompt template
    tools.py            # 3 tools: search, fetch, list_services
    graph.py            # LangGraph workflow + AgentSession
  ui/streamlit_app.py   # chat UI
  cli.py                # smoke-test REPL

infra/                  # Terraform: S3, ECR, IAM, App Runner
docker/                 # multi-stage Dockerfile
tests/                  # pytest, all offline (no Bedrock calls)
```

## Testing

```bash
make test       # pytest
make lint       # ruff
```

Tests are deliberately offline-safe. The chunker is pure Python, the
retriever round-trips FAISS in-process, and the tool tests stub the
retriever so no Bedrock calls leave the box. There is no end-to-end test
that hits live Bedrock; I tried with `moto` early on and gave up after
realizing it doesn't mock the Converse API.

## Known limitations

Stuff I deliberately didn't do because this is an interview submission, not
a production app:

- No auth. The App Runner URL is public.
- No streaming for the final answer (the tool-call trace does stream).
- The index is a single FAISS file. Fine for ~6k chunks, won't work for a
  big multi-tenant deployment.
- Conversation history lives in process memory. Restart the container and
  it's gone.
- No retry on partial ingest failures. If a clone or an embedding fails
  mid-run, you re-run the whole thing.

## Bugs I hit while building

Useful to know if you're iterating on this:

- Claude 3.5 Sonnet v2 on Bedrock is end-of-life; the obvious model ID
  fails. Use a current model and the `us.` inference-profile prefix.
- The awsdocs GitHub repos have an `archived` default branch with no content.
  Pin `branch: main` when cloning.
- Some `awsdocs/*` repos are gone entirely (Bedrock user guide, EC2). I
  swapped the source list to ones that still have content.
- AWS doc markdown has LaTeX-style escapes (`\.`, `\(`) and `<a name="..."></a>`
  anchor tags inside headers. The chunker strips both, otherwise they pollute
  embeddings and citation titles.
- LangGraph's `recursion_limit` is the right way to cap tool loops. I
  originally wrote a `force_finalize` node that injected a stop message,
  which Anthropic's Converse API rejects when there's an unresolved
  `tool_use` block. Switched to recursion_limit + a graceful fallback
  message.

## License

MIT. See `LICENSE`. AWS documentation has its own license; this project
indexes and quotes from it but doesn't redistribute it.
