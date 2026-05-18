# Scratch notes

Random things I want to remember between sessions. Not part of the
submission, but easier to keep here than in my head.

## Stuff that bit me

- Claude 3.5 Sonnet v2 on Bedrock is EOL. Use the `us.` inference profile
  for the 4.x models, the bare IDs reject on-demand throughput.
- awsdocs default branches got moved to `archived` (empty). The `branch:`
  field in sources.yaml is mandatory now.
- `force_finalize` node was a bad idea, Anthropic Converse rejects any
  attempt to insert a message between tool_use and tool_result. Replaced
  with recursion_limit.

## Things to try if I come back to this

- Reranker between retrieve and synthesize. Cohere rerank via Bedrock,
  probably 50ish LOC.
- Stream the final answer too (right now only the trace streams). Should
  be `astream_events` + `st.write_stream`.
- Eval harness: JSONL of (question, expected_url) pairs, score via
  LLM judge. Without this I have no way to know if a model swap regressed
  quality.
- Cache the embedding model client. Right now `TitanEmbedder()` builds a
  new boto3 client every time `Retriever._ensure_embedder()` lazy-inits,
  which is once per session, but cleaner to stash on the module.

## Open questions

- Is `INDEX_FLAT_IP` going to feel slow once the corpus is >50k chunks?
  Probably not on a single instance, but I haven't measured.
- Should ToolKit own the sources list or hand it back via the graph
  state? Right now it's a mutable attribute, which I keep flinching at.

## Cost watch

- 1 ingest of the current sources: ~$0.06 in Titan calls.
- 1 chat turn: ~5k tokens through Claude Sonnet 4.5, runs $0.01ish.
- App Runner: ~$60/mo at always-on 1 vCPU. Drop min_instances to 0 to
  scale to zero when nobody's using it.
