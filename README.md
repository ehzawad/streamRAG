# StreamRAG

A full-stack comparison of Naive RAG and StreamRAG for typed input. Naive waits
for **Send** before retrieval. StreamRAG can prepare evidence while the user is
typing, but it never generates or displays an answer before **Send**.

Both paths use the same local CRAG corpus, chunking, embeddings, search policy,
answer agent, memory, and scorer. Their runtime state and implementations remain
isolated.

## Experiences

| Route | Purpose |
|---|---|
| `/naive` | Run the conventional post-Send baseline |
| `/stream` | Run StreamRAG with pre-Send evidence preparation |
| `/compare` | Send the same committed text to both isolated services |

All routes support multi-turn conversations. Compare keeps separate Naive and
StreamRAG sessions so neither path can use the other's history or cache.

## Quick start

Requirements: Docker, `make`, and a real OpenAI API key.

```bash
cp .env.example .env
# Set OPENAI_API_KEY and ALLOW_UNREVIEWED_DATASET=1 in .env.

# terminal A
make docker-up

# terminal B, once for fresh volumes
make docker-sync
```

Open <http://127.0.0.1:5173/>. Qdrant runs as two private Docker services with
separate persistent volumes. SQLite is embedded in each API container and stored
in its own persistent volume; no SQLite server or host installation is required.

## Data and current evidence

- Corpus: 250 complete CRAG-derived documents committed as
  [`data/crag_eval/documents.jsonl.bz2`](data/crag_eval/documents.jsonl.bz2).
- Index: 400-token chunks with 50-token overlap, producing exactly 1,000 Qdrant
  points per path with `text-embedding-3-large`.
- Evaluation: 5 development questions and 10 held-out test questions, with gold
  answers in [`data/crag_eval/test_gold.jsonl`](data/crag_eval/test_gold.jsonl).
- Benchmark: one path (`make benchmark` then `make score`) run locally on the
  committed candidate corpus (`approval_status = candidate_pending_human_review`),
  loaded with `ALLOW_UNREVIEWED_DATASET=1`. There is no separate sealed/final run.

The committed real-API run completed 20/20 path outputs with no failures. Both
paths passed the automatic answer, support, and citation checks on all 10 test
questions. StreamRAG won every first-token race, with a median paired reduction
of 782 ms (42.1%). This is small-scale local evidence, not a final accuracy claim.

## Structure

| Directory | Ownership |
|---|---|
| `naive/` | independently runnable post-Send RAG path |
| `stream/` | independently runnable StreamRAG path for typed input |
| `native/` | Rust `snapshot_delta` hot path (PyO3), with a Python fallback |
| `shared/` | only behavior that must be identical across paths |
| `frontend/` | route hub and browser UI; no benchmark logic |
| `comparison/` | headless provisioning, replay, scoring, and artifacts |
| `data/crag_eval/` | committed corpus, questions, gold, checksums, and review sheet |

Removing `frontend/` leaves both APIs and the comparison CLI usable. Removing
`comparison/` leaves both APIs and the frontend usable. Neither RAG path imports
or calls the other.

StreamRAG's per-draft delta analysis (`SnapshotAnalyzer.analyze`, the pre-Send
hot path) is implemented in Rust under `native/snapshot_delta/` and loaded through
an import seam in `stream/snapshot.py`. When the `streamrag_snapshot` wheel is
absent the identical pure-Python implementation runs instead, so the module is a
measured speedup, not a dependency. Build it locally with `make native` and prove
parity plus the microbenchmark with `make bench-native`.

## Documentation

- [Dataset and human review](docs/DATASET.md)
- [Pipeline and architecture](docs/PIPELINE.md)
- [Run and reproduce](docs/RUN.md)
- [Benchmark report](docs/BENCHMARK_REPORT.md)

Component-specific commands remain in the README inside each component folder.

## Verify

```bash
make setup
make check
make docker-config
```

`make check` runs Python lint (`ruff`) and a production frontend build; the final
repo ships no pytest suite. The committed corpus is checksum-bound; each service
verifies those checksums when it loads the dataset, so no separate
data-verification step is required.

The local stack has no authentication and binds host ports to loopback. Do not
publish it unchanged: a network deployment needs TLS, identity and authorization,
rate and spend limits, protected administration, backups, and monitoring.

## Attribution

The corpus is derived from Meta's
[CRAG Task 1/2 development release](https://github.com/facebookresearch/CRAG)
under CC BY-NC 4.0. StreamRAG's scheduling idea is adapted to text input; this
project does not claim to reproduce the paper's speech stack, trained trigger,
reranker, or scale.
