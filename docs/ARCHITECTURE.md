# Architecture notes

Why each piece is the way it is. Written for the interview panel so they
don't have to read all the code.

This is a take-home prototype, not a production system. Anywhere I talk
about trade-offs, I'm not implying the chosen path is right at scale,
only that it was right for the size and time budget of this submission.

## The picture

```
            +--------------------------------------------------+
  browser   |              App Runner (public)                 |
   |        |                                                  |
   v        |  Streamlit  --->  LangGraph agent loop           |
 chat UI    |       ^             |                            |
            |       |             v                            |
   answer + |       |          3 tools                         |
   sources  |       |          (search, fetch, list)           |
            |       |             |                            |
            +-------|-------------|----------------------------+
                    |             v
                    |    +---------------+
                    |    | FAISS (in-mem)|<--- loads on boot
                    |    +-------+-------+
                    |            |
                    |            v
                    |        +-------+
                    |        |  S3   |  <--- `make ingest` uploads
                    |        +-------+
                    |
              Bedrock APIs:
                Claude Sonnet 4.5 (chat)
                Titan v2 (embeddings)
```

## The agent loop

LangGraph compiles to a tiny state machine with two real nodes (`agent`
and `tools`):

```
START -> agent --(tool calls?)--> tools -> agent ...
                |
                +--(no tool calls)--> END
```

The graph state is just the message history. Everything else (retrieved
sources, the per-turn trace) is derived from the messages by the UI
layer.

I picked LangGraph over a hand-rolled ReAct loop because:

1. Conditional edges are clearer than parsing `Thought:`/`Action:` lines.
2. The graph compiles, so the state schema is checked once instead of
   at every tool call.
3. `recursion_limit` is the standard way to cap loops, which matters
   because Anthropic's Converse API will reject any attempt to inject a
   "stop" message between an `AIMessage` with `tool_use` and its
   corresponding `tool_result`. I learned that the hard way.

The system prompt (`agent/prompts.py`) sets brevity rules and forbids
the model from writing its own "Sources" section, because the UI renders
citations separately. There's a regex in `graph.py` as a safety net for
when the model writes one anyway.

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
over the token budget (default 750), it gets split by paragraph windows
with a small overlap. Each chunk carries:

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
compute). No IVF/HNSW because at 6k vectors a flat index is already
sub-millisecond and there's no training step to get wrong.

Hosted vector DBs were not the right choice for a take-home:

- OpenSearch Serverless has a ~$700/mo minimum even idle.
- Aurora pgvector serverless v2 is cheaper (~$50/mo minimum) but still
  more than a take-home should cost.

FAISS in-process means every container instance loads its own copy of
the index into memory. That doesn't scale horizontally. For this
submission it doesn't need to.

## Tools

Three of them. Each one exists because of a specific behavior I want
from the agent.

| Tool | Why it's there |
| --- | --- |
| `search_aws_docs(query, service_filter?, k?)` | The default path. The system prompt tells the agent to use this first. |
| `fetch_aws_doc_page(url)` | Fallback for URLs the user pastes or services I haven't indexed. Host-restricted to `docs.aws.amazon.com` so the agent can't be tricked into SSRF. |
| `list_indexed_services()` | So the agent can disclose its own scope honestly. The system prompt also lists the services, but having it as a tool means the agent can re-check after long conversations. |

Deliberately not a tool: anything that calls AWS APIs (boto3, the CLI,
etc). The brief was about docs Q&A, not orchestrating real
infrastructure, and adding write tools would expand the threat model.

## Infrastructure

Terraform, four modules (`storage`, `ecr`, `iam`, `app_runner`). The
notable choices:

- **App Runner over ECS+ALB.** App Runner is the simplest happy path
  for "one stateless container, public URL." ECS gives more control
  but at the cost of VPC, ALB, target groups, security groups.
- **IAM scoped to two model ARNs and one bucket.** Not wildcards. If
  someone steals the role they can't enumerate Bedrock.
- **Two-phase apply.** App Runner refuses to create a service if the
  ECR image doesn't exist yet. The root module gates the App Runner
  resource behind `create_app_runner`, so you `apply` once, push the
  image, then `apply` again with the flag flipped.
- **Cross-region inference-profile permissions.** The IAM policy
  allows `inference-profile/*` because Claude 4.x on Bedrock requires
  invoking through a profile (the bare model IDs reject on-demand
  throughput).

There's no VPC. Bedrock and S3 are reached over public AWS endpoints,
which is fine for a public-internet Streamlit app and saves NAT money.

## Things that broke during the build

Useful context for whoever inherits this:

1. **Claude 3.5 Sonnet v2 hit EOL on Bedrock.** Had to swap to
   `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. The `us.` prefix is
   a cross-region inference profile, which newer Claude models on
   Bedrock require.
2. **awsdocs default branches got rotated to `archived`.** First
   ingest run produced 0 chunks until I noticed. Now pinned to `main`.
3. **Half the source repos I planned to use are gone or empty.**
   Rewrote `sources.yaml` against what's actually live.
4. **Force-finalize node violated tool_use/tool_result adjacency.** My
   first cut at the agent loop tried to inject a "stop" message when
   the tool-call budget ran out, which fails Anthropic validation. The
   fix was switching to LangGraph's `recursion_limit` and a graceful
   fallback message.
5. **AWS doc markdown contains LaTeX-style escapes and HTML anchor
   tags.** The chunker has a small regex pass for both.

## What's not designed for, on purpose

- **Multi-tenant scale.** Single FAISS file, in-process. Fine for one
  user, doesn't horizontally scale.
- **High availability.** One App Runner instance is enough for the
  demo. Configure `min_instances=2` if you actually care.
- **Conversation persistence across restarts.** History lives in
  process memory. LangGraph has checkpointer support if this matters
  to you.
- **Cost optimization.** App Runner is pay-per-second. Set
  `app_runner_min_instances=0` if you want it to sleep, and accept
  the ~30s cold start when the FAISS index reloads from S3.
- **Auth.** App Runner endpoint is public. Stick Cognito or basic
  auth in front of it before sharing the URL with anyone you don't
  trust.
