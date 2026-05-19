# Architecture

Why each piece is the way it is. Read this before diving into the code if
you want context on the design choices.

## The picture

```
            +--------------------------------------------------+
  browser   |              ALB (HTTP, public)                  |
   |        |                       |                          |
   v        |             stickysession routing                |
 chat UI    |                       v                          |
            |   +---------------------------------------+      |
   answer + |   |  ECS Fargate task (Streamlit + agent) |      |
   sources  |   |    LangGraph agent loop               |      |
            |   |    3 tools: search, fetch, list       |      |
            |   |    FAISS in-process (loaded from S3)  |      |
            |   +---------------------------------------+      |
            |                       |                          |
            +-----------------------|--------------------------+
                                    v
                ┌───────────────────┬──────────────────┐
                │                   │                  │
                ▼                   ▼                  ▼
     ┌────────────────┐    ┌────────────────┐  ┌────────────────┐
     │  Bedrock Chat  │    │ Bedrock Embed  │  │ docs.aws...    │
     │ Claude Sonnet  │    │  Titan v2 1k-d │  │ (fallback only │
     │     4.5        │    │                │  │  via fetch tool)│
     └────────────────┘    └────────────────┘  └────────────────┘
                                  ▲
                                  │ on container boot
                            ┌─────┴─────┐
                            │    S3     │
                            │ FAISS idx │
                            └───────────┘
                                  ▲
                                  │ make ingest
                            ┌─────┴─────────────────────────┐
                            │ Ingestion pipeline (local job)│
                            │ clone awsdocs -> chunk ->     │
                            │ embed -> FAISS -> upload S3   │
                            └───────────────────────────────┘
```

## The agent loop

LangGraph compiles to a small state machine with two real nodes (`agent`
and `tools`):

```
START -> agent --(tool calls?)--> tools -> agent ...
                |
                +--(no tool calls)--> END
```

State is the message history. Everything else (retrieved sources, the
per-turn trace events) is derived from the messages.

LangGraph over a hand-rolled ReAct loop because:

1. Conditional edges are clearer than parsing `Thought:`/`Action:` lines.
2. The graph compiles, so the state schema is checked once.
3. `recursion_limit` is the standard way to cap loops. This matters
   because Anthropic's Converse API rejects any attempt to inject a
   "stop" message between an `AIMessage` with `tool_use` and its
   corresponding `tool_result`.

The system prompt (`agent/prompts.py`) sets brevity rules and forbids the
model from writing its own "Sources" section, since the UI renders
citations separately. There's a regex in `graph.py` as a safety net for
when the model writes one anyway.

`AgentSession.stream_turn()` is a generator that emits `TraceStep` events
as each LangGraph node fires. The Streamlit UI subscribes to that
generator and renders the "thinking" trace live, which is what makes the
agent loop visible to users instead of just a black box.

## RAG

### Where docs come from

The `awsdocs/*` GitHub repos. They're AWS's own publishing source,
markdown, cleanly licensed, no scraping question. Caveats found during
implementation:

- Several repos have been emptied or deleted (`amazon-bedrock-user-guide`
  is gone, `amazon-ec2-user-guide` has no content).
- The remaining live repos default to an `archived` branch with nothing
  on it. The actual content sits on `main`. The ingestion script pins
  `branch: main` per source.

Current source list: S3, IAM, DynamoDB, RDS, SQS. About 6k chunks total.
Adding more is editing `config/sources.yaml` and re-running `make ingest`.

### Chunking

`rag/chunker.py` splits by markdown header first. If a section is still
over the token budget (default 750), it gets split into paragraph
windows with a small overlap. Each chunk carries:

- the service name (`s3`, `iam`, ...)
- the section path (`Working with buckets > Naming rules`)
- the source URL (markdown filename -> .html on docs.aws.amazon.com)

Header-first chunking is the bit that matters for retrieval quality.
Fixed-window chunks fall on awkward boundaries half the time.

The chunker also strips two awsdocs-specific noise patterns:

- LaTeX-style escapes (`\.`, `\(`, `\-`) the AWS XML-to-Markdown
  converter produces.
- `<a name="anchor"></a>` tags AWS inserts next to every header.

Without that pass, citations show up with `<a name="..."></a>` in the
title, which looks broken.

### Embeddings + index

Titan Text Embeddings v2 at 1024 dimensions. FAISS `IndexFlatIP` over
L2-normalized vectors (which is cosine similarity, just faster to
compute). No IVF/HNSW because at 6k vectors a flat index is sub-millisecond
and there's no training step to get wrong.

The corpus easily fits in memory, so the index is loaded into every
container instance on boot. That doesn't scale horizontally past one
copy per task. For larger corpora the `Retriever` interface is small
enough to swap in OpenSearch Serverless or Aurora pgvector without
touching anything else.

## Tools

Three of them. Each one exists for a specific behavior:

| Tool | Purpose |
|------|---------|
| `search_aws_docs(query, service_filter?, k?)` | Default path. The system prompt tells the agent to use this first. |
| `fetch_aws_doc_page(url)` | Fallback for URLs the user pastes or services we haven't indexed. Host-restricted to `docs.aws.amazon.com` so the agent can't be tricked into SSRF. |
| `list_indexed_services()` | So the agent can disclose its own scope honestly. The system prompt also lists the services, but exposing it as a tool means the agent can re-check after long conversations. |

Deliberately not a tool: anything that calls AWS APIs (boto3, the CLI,
etc). The agent is for documentation Q&A, not destructive ops. Adding
write tools would expand the threat model and obscure intent.

## Infrastructure

Terraform, four modules (`storage`, `ecr`, `iam`, `ecs_alb`).

### Why ECS Fargate + ALB and not App Runner

App Runner was my first choice. Failed in production.

App Runner's envoy proxy returns HTTP 403 on any request with
`Upgrade: websocket`. Streamlit's reactive UI is WebSocket-only (no
long-polling fallback), so the page HTML loads but the chat never
connects. This is a known limitation of App Runner.

ECS Fargate + Application Load Balancer is the replacement:

- ALB supports WebSocket natively (Layer 7 LB, transparent upgrade).
- Fargate handles the container scheduling without me running EC2.
- Default VPC + default subnets means no NAT gateway, no custom
  networking. Module is self-contained.

Trade-off: ECS+ALB runs about $50/mo idle (Fargate task ~$30, ALB ~$18)
versus App Runner's ~$60/mo. Slightly cheaper but with the proxy that
actually works.

### Notable choices in the Terraform

- **Default VPC, public subnets, public IP on tasks.** Saves a NAT
  gateway (~$30/mo). For real prod, put the tasks in private subnets
  behind a NAT and only the ALB in public.
- **`stickiness` enabled on the target group.** Streamlit holds a
  WebSocket per user; if a reconnect lands on a different replica, the
  session state is lost. Sticky cookies pin browsers to one task.
- **`idle_timeout = 120s` on the ALB.** WebSocket connections idle
  between user messages; the default 60s tears them down mid-chat.
- **IAM scoped to two Bedrock models** (chat + embedding) plus
  inference-profile ARNs, since cross-region inference is required for
  Claude 4.x. S3 read on the index bucket only.
- **Two-phase apply.** ECS won't start a task if the image isn't in
  ECR yet. The root module gates ECS behind `create_app_runner`
  (variable name kept from the App Runner attempt), so first apply
  provisions ECR + S3 + IAM, you push the image, then second apply
  spins up ECS.

## Things that broke during the build

Useful context for whoever picks this up:

1. **Claude 3.5 Sonnet v2 hit EOL on Bedrock.** Swapped to
   `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. The `us.` prefix is
   a cross-region inference profile, which Claude 4.x on Bedrock
   requires.
2. **awsdocs default branches got rotated to `archived`.** First ingest
   produced 0 chunks until I noticed. Now pinned to `main`.
3. **App Runner doesn't pass WebSocket upgrades.** Spent an hour
   debugging the Streamlit XSRF / CORS config before realizing the
   envoy proxy was the gate. Switched to ECS + ALB.
4. **Force-finalize node violated tool_use/tool_result adjacency.** My
   first cut at the agent loop tried to inject a "stop" message when
   the tool-call budget ran out, which fails Anthropic validation. The
   fix was switching to LangGraph's `recursion_limit` and a graceful
   fallback.
5. **AWS doc markdown contains LaTeX-style escapes and HTML anchor
   tags.** The chunker has a small regex pass for both.
6. **IAM in the deploying account explicitly denies
   `iam:UpdateAssumeRolePolicy`.** Plan B was to create a separate ECS
   task role rather than adding ECS to the existing App Runner role's
   trust policy.

## What's not designed for, on purpose

- **Multi-tenant scale.** Single FAISS file, in-process. Fine for one
  user, doesn't horizontally scale.
- **High availability.** One ECS task at desired_count=1. Bump it for
  real HA.
- **Conversation persistence across restarts.** History lives in
  process memory. LangGraph has checkpointer support if needed.
- **Cost optimization.** ALB is the surprise tax. For a hobby project
  Lightsail Containers ($10/mo) is cheaper. Doesn't fit IaC patterns
  as cleanly though.
- **Auth.** Public URL. Stick Cognito + an ALB listener rule in front
  of it before exposing to anyone untrusted.
