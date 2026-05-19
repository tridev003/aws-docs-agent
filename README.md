# aws-docs-agent

A chatbot that answers AWS questions by retrieving from the official AWS user
guides. The agent decides when to search, what to search for, and when it has
enough material to answer. Citations are shown alongside each response so you
can verify what the model claimed.

Built with LangGraph + Bedrock (Claude Sonnet 4.5 for chat, Titan v2 for
embeddings), FAISS for retrieval, Streamlit for the UI, and Terraform for the
AWS infra (ECS Fargate + ALB + CloudFront + ECR + S3).

**Live demo:** <https://d23387gokj24ge.cloudfront.net>

## Why this stack

- **Bedrock** for both chat and embeddings, so everything stays in one
  AWS account.
- **FAISS persisted to S3** instead of OpenSearch Serverless (which costs
  ~$700/mo even idle). Trade-off: doesn't horizontally scale, fine here.
- **ECS Fargate + ALB** instead of App Runner. App Runner's envoy proxy
  doesn't pass WebSocket upgrades, which Streamlit requires for its
  reactive UI. I tried App Runner first and it failed; details in the
  bug list below.
- **Streamlit** because the focus is the agent, not a custom frontend.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the longer reasoning.

## Quick start (local)

Need Python 3.11+, an AWS account with Bedrock available, and `aws` configured
(`aws configure` or SSO, doesn't matter which).

```bash
git clone https://github.com/tridev003/aws-docs-agent.git
cd aws-docs-agent

# Editable install. If you don't have python 3.11, `uv python install 3.11`
# is the fastest way to get there.
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Sanity check
pytest

# Build the FAISS index. Clones 5 awsdocs repos, embeds ~6k chunks via
# Titan, takes about 4-5 minutes. Costs ~$0.06.
make ingest

# Run the UI
make run
```

Open http://localhost:8501 and ask it something. Indexed services: S3, IAM,
DynamoDB, RDS, SQS. Anything outside that scope, the agent should say so
explicitly instead of guessing.

A tiny CLI is also available:

```bash
aws-docs-chat
```

## Deploy to AWS

Two-phase apply. ECS refuses to start a task if the image isn't in ECR yet,
so we provision ECR + S3 + IAM first, push the image, then turn on ECS.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit owner_email at minimum

terraform init
terraform apply                       # creates ECR + S3 + IAM (phase 1)

cd ..
make push-image                       # builds and pushes the container

INDEX_S3_BUCKET=$(terraform -chdir=infra output -raw index_bucket) make ingest

cd infra
terraform apply -var "deploy_app=true"   # spins up ECS+ALB (phase 2)
terraform output app_url              # public URL
```

To tear down: `terraform destroy -var "deploy_app=true"`. The ALB is
the dominant cost line item; destroying brings it back to ~$0.

### Estimated cost

| Service          | Driver                          | Estimate |
| ---------------- | ------------------------------- | -------- |
| ECS Fargate      | 1× 1vCPU / 2GB, always-on 30d   | ~$30     |
| ALB              | 1 ALB, low traffic, 30d         | ~$18     |
| CloudFront       | NA+EU edges, low traffic        | <$1      |
| Bedrock (Claude) | ~50 turns × ~5k tokens          | ~$2      |
| Bedrock (Titan)  | One-off ingest, ~6k chunks      | <$0.10   |
| S3 + ECR         | ~50 MB                          | <$0.10   |
| **Total**        |                                 | **~$50/mo** |

The ALB is the surprise tax; if you wanted to shave that, the lighter
alternative is **Lightsail Containers** (~$10/mo for nano) which also
supports WebSocket, but it doesn't fit IaC patterns as cleanly.

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
the real content stayed on `main`.

## Repo layout

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
    graph.py            # LangGraph workflow + AgentSession + TraceStep
  ui/streamlit_app.py   # chat UI with live agent trace
  cli.py                # REPL for quick smoke tests

infra/                  # Terraform: S3, ECR, IAM, ECS Fargate + ALB, CloudFront
docker/                 # multi-stage Dockerfile, linux/amd64
tests/                  # pytest, all offline (no Bedrock calls)
```

## Testing

```bash
make test       # pytest
make lint       # ruff
```

The test suite is offline. The chunker is pure Python, the retriever
round-trips FAISS in-process, and the tool tests stub the retriever so no
Bedrock calls leave the box. There is no end-to-end test that hits live
Bedrock; I tried with moto early on and gave up after realizing it doesn't
mock the Converse API.

## Known limitations

Stuff that's deliberately not in this build:

- No auth on the public URL. Stick Cognito + an ALB listener rule in
  front before sharing it with anyone you don't trust.
- No streaming for the final answer (the tool-call trace does stream).
- Index is a single FAISS file. Fine for ~6k chunks, won't work for a
  big multi-tenant deployment.
- Conversation history lives in process memory. Restart the container
  and it's gone.
- No retry on partial ingest failures. If a clone or embedding fails
  mid-run, you re-run the whole thing.

## Things that broke while building

Useful to know if you're iterating on this:

- **Claude 3.5 Sonnet v2 on Bedrock is end-of-life.** The obvious model
  ID returns ResourceNotFoundException. Use a current model and the
  `us.` inference-profile prefix.
- **App Runner doesn't support WebSocket upgrades.** Streamlit needs
  WebSocket for its reactive UI; App Runner's envoy proxy responds 403
  on `Upgrade: websocket`. Hence ECS + ALB.
- **awsdocs default branches got rotated to `archived`.** Pin
  `branch: main` per source when cloning.
- **Three of the awsdocs repos I planned to use are gone or empty.**
  Swapped `sources.yaml` to ones that still have content.
- **AWS doc markdown contains LaTeX-style escapes** (`\.`, `\(`) and
  `<a name="..."></a>` anchor tags inside headers. The chunker has a
  cleanup pass for both, otherwise they pollute embeddings and citation
  titles.
- **LangGraph `recursion_limit` is the right way to cap tool loops.** I
  originally wrote a `force_finalize` node that injected a stop message
  mid-conversation, which Anthropic's Converse API rejects when there's
  an unresolved `tool_use` block.

## License

MIT. See `LICENSE`. AWS documentation has its own license; this project
indexes and quotes from it but doesn't redistribute it.
