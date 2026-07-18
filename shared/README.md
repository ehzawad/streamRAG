# Shared infrastructure

`shared/` is the common platform layer, not a third RAG product. It contains only
behavior that must be identical for a fair comparison or can serve either app
without knowing which one is active.

It owns:

- API lifecycle, commit/events routes, persistence, and the single-product UI
  shell;
- settings and source, configuration, and dataset fingerprints;
- CRAG checksums, chunking, embeddings, index readiness, and search;
- grounded answers, the strict local-corpus tool, memory, and usage/cost
  accounting;
- schemas and the versioned telemetry contract.

Naive owns committed-text retrieval policy. Stream owns draft analysis,
triggering, speculation, and reuse. `frontend/` owns the GUI; `comparison/` owns
headless replay and scoring. `shared/` imports none of them and is not runnable
by itself.

```bash
uv run pytest -q shared/tests
```
