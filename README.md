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
- Evaluation: 5 visible development questions and 10 sealed test questions.
- Status: `candidate_pending_human_review`; the sealed final benchmark has not
  been run.

The retained real-API development run completed 10/10 path outputs. Both paths
scored 5/5 on the automatic answer, support, and citation checks. StreamRAG won
four of five first-token races, with a median paired reduction of 676.552 ms
(28.093%). The late-stabilizing case was slower. This is development evidence,
not a final accuracy claim.

## Structure

| Directory | Ownership |
|---|---|
| `naive/` | independently runnable post-Send RAG path |
| `stream/` | independently runnable StreamRAG path for typed input |
| `shared/` | only behavior that must be identical across paths |
| `frontend/` | route hub and browser UI; no benchmark logic |
| `comparison/` | headless provisioning, replay, scoring, and artifacts |
| `data/crag_eval/` | committed corpus, questions, gold, checksums, and review sheet |

Removing `frontend/` leaves both APIs and the comparison CLI usable. Removing
`comparison/` leaves both APIs and the frontend usable. Neither RAG path imports
or calls the other.

## Documentation

- [Dataset and human review](docs/DATASET.md)
- [Pipeline and architecture](docs/PIPELINE.md)
- [Run and reproduce](docs/RUN.md)
- [Benchmark report](docs/BENCHMARK_REPORT.md)

Component-specific commands remain in the README inside each component folder.

## Verify

```bash
make setup
make verify-data
make check
make docker-config
```

The local stack has no authentication and binds host ports to loopback. Do not
publish it unchanged: a network deployment needs TLS, identity and authorization,
rate and spend limits, protected administration, backups, and monitoring.

## Attribution

The corpus is derived from Meta's
[CRAG Task 1/2 development release](https://github.com/facebookresearch/CRAG)
under CC BY-NC 4.0. StreamRAG's scheduling idea is adapted to text input; this
project does not claim to reproduce the paper's speech stack, trained trigger,
reranker, or scale.
