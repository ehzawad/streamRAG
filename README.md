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
# Set OPENAI_API_KEY in .env.

# terminal A
make docker-up

# terminal B, once for fresh volumes
make docker-sync
```

Open <http://127.0.0.1:5173/>. Qdrant runs as two private Docker services with
separate persistent volumes. SQLite is embedded in each API container and stored
in its own persistent volume; no SQLite server or host installation is required.

## Data

- Corpus: 250 complete CRAG-derived documents committed as
  [`data/crag_eval/documents.jsonl.bz2`](data/crag_eval/documents.jsonl.bz2),
  checksum-bound; each service verifies the checksums when it loads the dataset.
- Index: 400-token chunks with 50-token overlap, producing exactly 1,000 Qdrant
  points per path with `text-embedding-3-large`.
- Evaluation: 5 development questions and 10 held-out test questions, with gold
  answers in [`data/crag_eval/test_gold.jsonl`](data/crag_eval/test_gold.jsonl).
- `candidate_pending_human_review` means the dataset is proposed but not frozen.

## Structure

| Directory | Ownership |
|---|---|
| `naive/` | independently runnable post-Send RAG path |
| `stream/` | independently runnable StreamRAG path for typed input |
| `shared/` | only behavior that must be identical across paths |
| `frontend/` | route hub and browser UI; no benchmark logic |
| `comparison/` | headless provisioning, replay, scoring, and artifacts |
| `deployment/` | optional Modal deployment |
| `data/crag_eval/` | committed corpus, questions, gold, and checksums |

Removing `frontend/` leaves both APIs and the comparison CLI usable. Removing
`comparison/` leaves both APIs and the frontend usable. Neither RAG path imports
or calls the other.

## Evaluation and deployment boundary

Gold is not read by either service or by the single benchmark runner.
The offline scorer reads it only after predictions are finalized and hashed.
Automatic answer and citation checks are not presented as human semantic accuracy.
Missing provider usage is marked as a lower bound, never counted as zero.
The data does not establish a universal speed, accuracy, or cost improvement, and
results from the StreamRAG paper are not treated as results of this implementation.

The local stack has no authentication and binds host ports to loopback. Do not
publish it unchanged: a network deployment needs TLS, identity and authorization,
rate and spend limits, protected administration, backups, and monitoring.
Anyone who can reach an API can submit requests, consume OpenAI budget, and read
results. Changing a bind address alone is not a deployment plan.

## Verify

```bash
make setup
make check
make docker-config
```

`make check` runs Python lint (`ruff`) and a production frontend build. Run and
reproduction commands are in [docs/RUN.md](docs/RUN.md).

## Attribution

The corpus is derived from Meta's
[CRAG Task 1/2 development release](https://github.com/facebookresearch/CRAG)
under CC BY-NC 4.0. StreamRAG's scheduling idea is adapted to text input; this
project does not claim to reproduce the paper's speech stack, trained trigger,
reranker, or scale.
