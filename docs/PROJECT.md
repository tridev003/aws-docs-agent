# aws-docs-agent: Project Documentation

A walkthrough of the system, end to end. If you read only one document in
this repo, read this one.

**Live demo:** <http://aws-docs-agent-dev-alb-1873847918.us-east-1.elb.amazonaws.com>
**Source:** <https://github.com/tridev003/aws-docs-agent>

---

## 1. What it does

`aws-docs-agent` is an agentic chatbot that answers natural-language
questions about AWS services. The agent:

1. Receives a user's question through a Streamlit chat UI.
2. Decides on its own whether to search the indexed AWS documentation,
   fetch a specific page by URL, or list its indexed scope.
3. Issues one or more retrieval calls against a FAISS vector index built
   from the official AWS user guides.
4. Synthesizes a structured, conversational answer grounded in the
   retrieved doc chunks.
5. Renders the answer alongside a deduplicated list of clickable source
   citations and a live trace of which tools it called and why.

Five AWS services are pre-indexed: **S3, IAM, DynamoDB, RDS, SQS**
(~6,000 doc chunks total). Adding a service is editing one YAML file
and re-running the ingestion pipeline.

The system is built and deployed entirely on AWS:

- **Amazon Bedrock** for both the LLM (Claude Sonnet 4.5) and embeddings
  (Titan v2)
- **Amazon ECS Fargate + Application Load Balancer** for the public web app
- **Amazon ECR** for the container image
- **Amazon S3** as the persistent store for the FAISS index
- **AWS IAM** roles, scoped to the specific model ARNs and bucket prefix

The entire stack is described in Terraform, in four reusable modules.

---

## 2. How it demonstrates each capability

The original brief asked the solution to demonstrate six things. Here is
where each one lives in the code.

### 2.1 Strong understanding of LLM-based applications

- **`bedrock/client.py`** wraps the Bedrock runtime with retries,
  configurable model IDs, and a LangChain-compatible chat factory. The
  Converse API is used so the agent code is independent of any one model
  family (Claude / Llama / Mistral all work behind the same interface).
- **`agent/prompts.py`** is a single system prompt template. It is
  re-rendered every turn so the indexed-service list and today's date
  stay current. The prompt explicitly disallows hallucinated source
  sections and bounds the response length to a chat-style 150–350 words.
- **Cross-region inference profiles** (the `us.` prefix on the model ID)
  are required by newer Anthropic models on Bedrock. The IAM module
  scopes permissions to both the inference-profile ARN and the
  underlying foundation-model ARNs across regions, which is a non-obvious
  detail not in most blog posts.
- **Tool budget enforcement** uses LangGraph's `recursion_limit`. A
  custom force-finalize node was tried and abandoned because Anthropic's
  Converse API rejects any message inserted between `tool_use` and its
  corresponding `tool_result`.

### 2.2 Agentic workflow implementation

The agent is a LangGraph state machine with two nodes:

```
START -> agent --(tool calls?)--> tools -> agent ...
                |
                +--(no tool calls)--> END
```

Concretely:

- **`agent/graph.py`** compiles the graph. State is a typed message list
  plus the `add_messages` reducer.
- **`agent` node** invokes `ChatBedrockConverse` bound to the three
  tools. The LLM decides whether to emit `tool_use` blocks or a final
  answer.
- **`tools` node** is LangGraph's prebuilt `ToolNode`. It serializes each
  tool result back into the message log as a `ToolMessage`.
- **`stream_turn()`** is a generator method that yields `TraceStep` events
  as each node fires (plan / tool_call / tool_result / compose / warn).
  The Streamlit UI subscribes to that stream and shows the live agent
  trace as it happens, so the agentic behavior is visible, not hidden.
- **Three tools** are bound to the agent:
  - `search_aws_docs(query, service_filter?, k?)` — semantic search over
    the FAISS index. Optionally scoped to a service.
  - `fetch_aws_doc_page(url)` — host-restricted fetch on
    `docs.aws.amazon.com`. SSRF-safe.
  - `list_indexed_services()` — disclosure of scope so the agent can
    honestly admit when a question is out of bounds.

The agent does not have any AWS-API-calling tool. The system is
read-only by design.

### 2.3 Retrieval-Augmented Generation (RAG) concepts

The RAG pipeline is in `src/aws_docs_agent/rag/`:

- **`chunker.py`** splits markdown by header first, with a paragraph
  window fallback for oversized sections. Each chunk carries its
  service tag, breadcrumb section path (`Working with buckets > Naming rules`),
  and the canonical docs URL. The chunker also strips two awsdocs
  noise patterns: LaTeX-style escapes (`\.`, `\(`) and HTML anchor
  tags (`<a name="..."></a>`) that AWS's XML→Markdown converter
  emits.
- **`retriever.py`** wraps a FAISS `IndexFlatIP` over L2-normalized
  vectors (which is cosine similarity, computed faster). Persisted as
  three files: `faiss.index`, `metadata.jsonl`, `manifest.json`. The
  retriever supports per-service filtering with k-oversampling so the
  result count is stable.
- **`ingest.py`** is the end-to-end pipeline: clone each awsdocs repo
  (shallow, pinned to `branch: main` because AWS rotated several
  defaults to `archived`), chunk, embed via Titan v2 with a thread
  pool, write a FAISS index, atomically swap it into place, and
  optionally upload to S3.

Retrieval quality choices:

- Header-first chunking produces self-contained snippets. Citations
  surface the section heading so users see *where* in the docs the
  answer came from.
- Per-service filtering lets the agent narrow searches when the
  question is about one specific service.
- Dedup-by-URL on the result set, so the citation panel doesn't show
  three near-duplicate hits from the same doc page.
- Optional rerank stage is out of scope for v1 but the abstraction is
  small enough to drop one in (Cohere rerank via Bedrock would be
  ~50 lines).

### 2.4 Natural-language query handling

Three classes of input the agent handles distinctly:

| Class | Behavior |
|-------|----------|
| In-scope how-to (e.g. "enable versioning on S3 with the CLI") | Searches docs once or twice, returns the command verbatim with a short explanation. |
| In-scope conceptual (e.g. "user vs role") | Searches once, returns a structured comparison with bullets. |
| Out-of-scope (e.g. "EKS cluster size") | Discloses the indexed scope, suggests adding the service to `sources.yaml`, refuses to guess a number. |
| User-supplied URL | Calls `fetch_aws_doc_page` (host-restricted), summarizes the page. |
| Destructive intent (e.g. "delete all my S3 buckets") | Refuses. This is a docs Q&A agent, not an orchestration agent. |

The system prompt enforces a uniform response style: 1–2 sentence opener,
then structured expansion with `##` sub-headers, bullets, and code blocks
where appropriate. Target length is 150–350 words. Filler ("Great
question!") is forbidden.

### 2.5 Integration and interaction with AWS documentation

The data source is the `awsdocs/*` GitHub organization, which is AWS's
own publishing pipeline. Markdown is the source format for the public
HTML docs at `docs.aws.amazon.com`, which means filenames map deterministically
to public URLs (`foo.md` → `https://docs.aws.amazon.com/<service>/.../foo.html`).
Each chunk carries that URL, and the Sources panel in the UI links
straight back to it.

Two gotchas the project surfaces:

1. **AWS rotated the default branches** on several awsdocs repos to a
   placeholder branch named `archived` (empty), while the real content
   stays on `main`. The ingestion config now pins `branch: main`
   explicitly per source.
2. **A handful of awsdocs repos are entirely deleted or thinned.**
   `amazon-bedrock-user-guide` is gone, `amazon-ec2-user-guide` is
   empty, `aws-lambda-developer-guide` is mostly stub. The source list
   is documented with the last known good state and a note next to
   dead candidates.

The agent's fallback path (`fetch_aws_doc_page`) lets it answer
questions about services that aren't in the index, as long as the
user pastes a `docs.aws.amazon.com` URL. Non-AWS hosts are refused.

### 2.6 Clean architecture and scalable design practices

The codebase has one direction of dependency: UI → agent → tools →
retriever → bedrock. No cycles. Each layer is independently testable.

Specific practices:

- **Single config surface.** `pydantic-settings` reads env vars (and
  `.env`) once. The rest of the code calls `get_settings()`. No
  scattered `os.environ` lookups.
- **Vendor SDK isolation.** Bedrock lives behind `bedrock/client.py`.
  Switching providers touches one file.
- **Narrow state.** The LangGraph state is just `messages`. Sources
  and the trace are derived. Hard to corrupt.
- **Offline-safe tests.** All 15 unit tests run without network or
  AWS access. The chunker is pure Python, the retriever round-trips
  FAISS in-process, the tool tests stub the retriever.
- **Modular IaC.** Four Terraform modules with clean inputs and
  outputs: `storage`, `ecr`, `iam`, `ecs_alb`. Each module declares
  its own variables and outputs, can be reused across stacks.
- **Least-privilege IAM.** Bedrock perms scoped to the chat and
  embedding model ARNs plus the inference-profile ARN. S3 read on the
  one bucket. No wildcards in the workload role.
- **Two-phase apply.** The IaC creates infra without the running
  service first (ECR + S3 + IAM), the image is pushed, then a second
  apply starts ECS pointing at the now-present image. Avoids the
  classic "service refuses to create because the image isn't there"
  failure mode.
- **Atomic ingestion.** The pipeline builds the index in a temp dir
  and atomically swaps it in. A Ctrl-C doesn't leave a partial index.
- **Sticky sessions on the ALB.** Streamlit's WebSocket session state
  is per-task; sticky cookies keep a browser pinned to one replica so
  reconnects don't lose state.

Known scaling limits (called out honestly in `docs/ARCHITECTURE.md`):

- FAISS in-process doesn't horizontally scale. The `Retriever`
  abstraction is small enough to swap in OpenSearch Serverless or
  pgvector when the corpus grows past ~100k chunks.
- Conversation state is in-memory. LangGraph supports
  checkpointers (Sqlite, Postgres, Redis) for cross-restart persistence.
- One ECS task at `desired_count=1`. For HA, bump it; the sticky
  session config already handles user pinning.

---

## 3. How to run it

### 3.1 Local development

Requires Python 3.11+, an AWS account with Bedrock available, and `aws`
configured (`aws configure` or SSO).

```bash
git clone https://github.com/tridev003/aws-docs-agent.git
cd aws-docs-agent

python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Unit tests (offline, no AWS calls)
pytest

# Build the FAISS index. ~5 min, ~$0.06 in Titan calls.
make ingest

# Run the chat UI
make run                              # http://localhost:8501
```

A REPL is also available for quick smoke tests:

```bash
aws-docs-chat
```

### 3.2 Configuration

Everything flows through `.env` (read by `pydantic-settings`):

| Variable                 | Purpose                                                  |
|--------------------------|----------------------------------------------------------|
| `AWS_REGION`             | Bedrock region. Default `us-east-1`.                     |
| `BEDROCK_CHAT_MODEL_ID`  | Chat model. Default Claude Sonnet 4.5 (us. inference profile). |
| `BEDROCK_EMBED_MODEL_ID` | Embedding model. Default Titan v2 1024-d.                |
| `INDEX_S3_BUCKET`        | Optional. If set, the app hydrates the FAISS index from S3 on boot. |
| `INDEX_S3_PREFIX`        | Key prefix under the bucket. Default `faiss/`.            |
| `AGENT_MAX_TOOL_CALLS`   | Hard cap on tool calls per turn. Default 10.             |
| `AGENT_TOP_K`            | Retrieval breadth. Default 6.                            |
| `INGEST_CHUNK_TOKENS`    | Chunker target size. Default 750.                        |
| `INGEST_MAX_FILES_PER_REPO` | Cap on files per source repo. Default 400.             |

### 3.3 Adding indexed services

Edit `config/sources.yaml`. Each entry:

```yaml
- service: dynamodb                                                  # short slug
  display_name: Amazon DynamoDB                                      # shown to users
  repo: https://github.com/awsdocs/amazon-dynamodb-developer-guide.git
  branch: main                                                       # IMPORTANT: AWS rotated defaults to `archived`
  subpath: doc_source                                                # path under the repo where markdown lives
  base_doc_url: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/  # public docs URL prefix
```

Re-run `make ingest`. The agent's system prompt is rendered each turn
with the live indexed-service list, so the change is picked up
without a restart in dev.

### 3.4 Deploying to AWS

Prerequisites: Terraform >=1.6, Docker Desktop (for `make push-image`).

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit owner_email at minimum

terraform init
terraform apply                          # phase 1: ECR + S3 + IAM only
```

After phase 1, push the image and upload the FAISS index:

```bash
cd ..
make push-image                          # builds linux/amd64, pushes to ECR

INDEX_S3_BUCKET=$(terraform -chdir=infra output -raw index_bucket) make ingest
```

Then phase 2:

```bash
cd infra
terraform apply -var "create_app_runner=true"
terraform output app_url                  # public URL
```

Tear-down: `terraform destroy -var "create_app_runner=true"`.

### 3.5 Costs (rough, 30-day always-on)

| Service          | Driver                          | Estimate |
|------------------|---------------------------------|----------|
| ECS Fargate      | 1× 1 vCPU / 2 GB                | ~$30     |
| ALB              | 1 ALB, low traffic              | ~$18     |
| Bedrock (Claude) | ~50 turns × ~5k tokens          | ~$2      |
| Bedrock (Titan)  | One-off ingest, ~6k chunks      | <$0.10   |
| S3 + ECR         | ~50 MB                          | <$0.10   |
| **Total**        |                                 | **~$50/mo** |

`terraform destroy` returns this to ~$0.

---

## 4. Architecture decisions worth calling out

### Why ECS Fargate + ALB (not App Runner)

App Runner was the original choice. It fails for Streamlit because its
envoy proxy doesn't pass `Upgrade: websocket` requests (responds 403).
Streamlit requires WebSocket for its reactive UI; there is no
long-polling fallback. The HTML loads, the chat panel never connects.

ECS Fargate + ALB is the replacement. The ALB supports WebSocket
natively, sticky sessions keep users pinned to one task, and Fargate
handles the container lifecycle without me running EC2.

Cost is comparable (~$50 vs. ~$60/mo). Worth it for the working chat.

### Why FAISS-in-S3 (not OpenSearch Serverless)

OpenSearch Serverless has a ~$700/mo minimum capacity charge even
when idle. For a 6k-chunk corpus that's a ridiculous bill. FAISS
in-process is sub-millisecond at this scale, free, and the
`VectorStore` abstraction is small enough that swapping to OpenSearch
or pgvector when the corpus grows is a one-day change.

Trade-off accepted: every ECS task loads its own copy of the index
into memory. Doesn't scale horizontally past one copy per task.

### Why Bedrock for both LLM and embeddings

The whole stack is on AWS, the brief is about AWS, and having one
provider means one IAM role, one set of model versions to track, and
predictable billing. Cross-region inference profiles are required for
Claude 4.x, which adds an IAM wrinkle but doesn't change the choice.

### Why Streamlit (not a custom frontend)

The agent is the interesting part of this project, not the UI.
Streamlit gives a chat interface, status panels, and source rendering
out of the box. The WebSocket dependency was the surprise cost (see
the App Runner story above).

### Why one system prompt, no fine-tuning

For five indexed services and three tools, a single ~400-token prompt
covers the entire behavior space. Few-shot examples or fine-tuning
would be premature at this scale. The prompt is rendered with the live
indexed-service list each turn so the model's "scope awareness" stays
accurate as the index changes.

### Why agentic loop (not single-shot RAG)

Single-shot RAG works for trivial questions but breaks on:

- Multi-service queries (e.g. "what IAM perms does Lambda need to
  write to S3?") which need at least two retrievals.
- Out-of-scope questions where the agent should disclose limits, not
  hallucinate.
- User-supplied URLs that aren't in the index.

The agent loop handles all three. The tool budget plus
`recursion_limit` keeps it from looping forever.

---

## 5. Repository layout

```
aws-docs-agent/
  src/aws_docs_agent/
    config.py                pydantic-settings, single config surface
    bedrock/client.py        Bedrock chat + embedding wrappers
    rag/
      chunker.py             markdown-aware section chunker
      retriever.py           FAISS + S3 hydration
      ingest.py              clone awsdocs, chunk, embed, persist
    agent/
      prompts.py             system prompt template
      tools.py               search, fetch, list tools + ToolKit
      graph.py               LangGraph workflow + AgentSession + stream_turn
    ui/streamlit_app.py      chat UI, live agent trace, sources panel
    cli.py                   REPL for quick smoke tests

  infra/
    main.tf                  composes the four modules
    variables.tf, outputs.tf, providers.tf, versions.tf
    modules/
      storage/               S3 bucket for FAISS index
      ecr/                   ECR repository
      iam/                   workload role + ECS execution + ECS task role
      ecs_alb/               ECS Fargate cluster/service/task + ALB

  docker/Dockerfile          multi-stage Python 3.11 image
  config/sources.yaml        which awsdocs repos to index
  tests/                     pytest, offline (no Bedrock)
  docs/
    ARCHITECTURE.md          design rationale, sequence diagrams
    DEMO.md                  prompts to demo the system
    PROJECT.md               this file
  scripts/smoke_test.py      end-to-end non-interactive test
  Makefile                   install / ingest / run / test / docker / tf
```

---

## 6. Testing

The full test suite is offline-safe:

```bash
make test          # pytest, 15 tests, ~0.3s
make lint          # ruff
```

Coverage by area:

- **Chunker** (`tests/test_chunker.py`, 7 tests): header parsing,
  code-fence handling, oversized section splitting, link/anchor
  stripping, metadata shape.
- **Retriever** (`tests/test_retriever.py`, 3 tests): FAISS round-trip,
  dimension mismatch errors, service filtering with stubbed embedder.
- **Tools** (`tests/test_tools.py`, 5 tests): indexed-service listing,
  source recording on search, service filter pass-through, fetch host
  restriction, empty-index handling.

An end-to-end script (`scripts/smoke_test.py`) hits live Bedrock for
sanity-checking deploys; it requires the index built and a working
AWS profile. Not part of `make test` because it's not free or offline.

---

## 7. Known limitations

Things deliberately not in scope for this build:

- **No auth.** The public URL has no access control. Stick Cognito or
  basic auth in front before exposing it to anyone untrusted.
- **No streaming for the final answer.** Tool calls stream as trace
  events, but the answer itself arrives as one chunk. An
  `astream_events` rewrite would fix this.
- **Single FAISS file.** Fine at 6k chunks, breaks past a few hundred
  thousand. Swap to OpenSearch / pgvector when needed.
- **In-process conversation history.** Restart the container and
  history is gone. LangGraph checkpointers solve this.
- **No retry on partial ingest failures.** If a clone or an embedding
  request fails mid-run, re-run the whole pipeline. Chunked
  checkpointing would help.
- **No reranker.** Cohere rerank via Bedrock between retrieve and
  synthesize is the obvious next precision lift. ~50 LOC.
- **One ALB listener, HTTP only.** For HTTPS, attach an ACM cert via
  Route53 / a custom domain.

---

## 8. Things that went wrong while building

Useful war stories for anyone iterating on this:

1. **Claude 3.5 Sonnet v2 on Bedrock is end-of-life.** The obvious
   model ID returns `ResourceNotFoundException`. Use a current model
   and the `us.` inference-profile prefix.
2. **awsdocs default branches got rotated to `archived`.** First
   ingest produced 0 chunks. Pin `branch: main` per source.
3. **Three of the awsdocs repos I planned to use are gone or empty.**
   Rewrote `sources.yaml` against what's actually live.
4. **App Runner doesn't pass WebSocket upgrades.** Spent an hour
   debugging Streamlit XSRF / CORS settings before realizing it was
   the envoy proxy. Switched to ECS + ALB.
5. **Force-finalize node violated Anthropic's tool_use/tool_result
   adjacency rule.** The agent loop now uses `recursion_limit` and a
   graceful fallback message instead.
6. **AWS doc markdown contains LaTeX-style escapes** (`\.`, `\(`) and
   `<a name="anchor"></a>` HTML tags. The chunker has a cleanup pass
   for both.
7. **The deploying IAM principal explicitly denies
   `iam:UpdateAssumeRolePolicy`.** Created a separate ECS task role
   rather than adding ECS to the existing App Runner role's trust
   policy.

---

## 9. Submission checklist

- ✅ **Source code repo:** <https://github.com/tridev003/aws-docs-agent>
- ✅ **Setup + execution instructions:** `README.md`, section 3 above
- ✅ **Architecture / design explanation:** `docs/ARCHITECTURE.md`, this
  document, and inline module docstrings
- ✅ **Deployed link:**
  <http://aws-docs-agent-dev-alb-1873847918.us-east-1.elb.amazonaws.com>

---

## 10. License

MIT. See `LICENSE`. AWS documentation has its own license; this project
indexes and quotes from it but does not redistribute it.
