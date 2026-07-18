# Shared infrastructure

`shared/` is a library, not a third RAG implementation. It contains only behavior
that must remain identical for a fair comparison or is reusable by either service
without knowing which path is active.

## Owned here

- FastAPI lifecycle, commit/events routes, and the capability-driven same-origin
  single-path UI shell
- immutable common settings and source/config/dataset fingerprints
- CRAG loading, checksum verification, deterministic chunking, embeddings, index
  readiness, and vector search
- grounded PydanticAI answer generation, strict local-corpus tool, token/cost
  accounting, conversation memory, and summarization
- API schemas, event delivery, persistence, and the versioned `RagPath` telemetry
  contract

The UI shell is shareable because it renders one service described by that
service's advertised capabilities. A Naive service never exposes or calls the
snapshot flow; a Stream service does. It contains no Naive/Stream switch and no
A/B orchestration.

## Not owned here

- Naive committed-text retrieval policy
- Stream snapshot analysis, trigger, speculative scheduler, or reuse policy
- Stream trigger/speculation settings and validation
- A/B request coordination, scoring, reports, or comparison UI

`shared/` must not import `naive`, `stream`, or `comparison`. It is expected to be
present with either implementation; it is not expected to run by itself.

Run its contract and infrastructure tests with:

```bash
uv run pytest -q shared/tests
```
