# Typed StreamRAG assessment

A working full-stack comparison of Naive RAG and Typed StreamRAG. Naive waits
for Send; Stream uses the typing window to prepare evidence and still answers
only after Send.

It adapts the StreamRAG scheduling idea to typed input; speech and a trained
trigger are out of scope.

## Fixed data and current result

- **Knowledge base:** 250 complete CRAG-derived documents, deterministically
  split into exactly 1,000 chunks/local Qdrant points.
- **Retrieval:** `text-embedding-3-large` at 3,072 dimensions. Embedded Qdrant
  needs no account or API key.
- **Evaluation:** 5 development questions and 10 sealed, unseen test questions.
  The only dataset is [`data/crag_eval`](data/crag_eval).
- **Gate:** status is `candidate_pending_human_review`. Development checks are
  allowed; the sealed test set cannot run until a reviewer approves and freezes
  the dataset and its checksums.

The latest real-API development smoke run had equal 100% automatic answer and
citation proxies. Stream won first-token latency on 5/5 questions, with a median
784.373 ms (40.160%) improvement. This is directional development evidence,
not a final benchmark: the sample is small, no answers were human-adjudicated,
and the sealed test set remains untouched. See
[`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md).

## One homepage, three experiences

| Experience | URL | Backend dependency |
| --- | --- | --- |
| Homepage | <http://127.0.0.1:5173/> | route launcher |
| Naive only | <http://127.0.0.1:5173/naive> | `naive/` + `shared/` |
| Stream only | <http://127.0.0.1:5173/stream> | `stream/` + `shared/` |
| Side-by-side | <http://127.0.0.1:5173/compare> | both isolated APIs |

`naive/` and `stream/` are independently runnable products. Neither imports or
calls the other. Their direct developer UIs remain available on ports 8001 and
8002. `frontend/` is only the GUI; its same-origin proxy keeps those ports out of
the public browser contract.
`comparison/` is a separate headless CLI for provisioning, replay, scoring, and
reports. Removing either consumer does not affect the APIs or the other consumer.
`shared/` holds only the corpus/index, answer, memory, API lifecycle, single-path
UI shell, and metric contracts that must be common for a fair test.

All three routed experiences are multi-turn chats. A follow-up keeps the prior
turns in that path's isolated session; **New chat** clears the transcript and
starts a new session. Compare preserves separate Naive and Stream histories so
neither implementation can borrow the other's state.

## Run the complete comparison

```bash
cp .env.example .env
# Set a real OPENAI_API_KEY in .env.
make setup
make verify-data
make dev-stack
```

In another terminal, run `make sync-app` once, then open the homepage at
<http://127.0.0.1:5173/>. Each API builds and owns its own Qdrant, SQLite,
metrics, session, and cache state under `var/`. Live indexing and answers use
OpenAI and local vector search; no live-path dependency is mocked. The faster
stopped-seed cloning workflow belongs only to the headless benchmark.

Exact standalone commands are in [`naive/README.md`](naive/README.md) and
[`stream/README.md`](stream/README.md). Component contracts are in
[`shared/README.md`](shared/README.md) and
[`comparison/README.md`](comparison/README.md); GUI commands are in
[`frontend/README.md`](frontend/README.md).

## Correctness boundary

Stream receives changed drafts and may retrieve after the latest delivered draft
has been unchanged for 500 ms. Draft evidence stays private. At Send, Stream can
reuse it only when the recorded draft exactly matches the committed text;
otherwise it performs the same committed-text retrieval as Naive. Both paths use
the same answer model, prompt, corpus, chunker, embeddings, search policy, memory,
and scorer.

## Run the development benchmark

```bash
make verify-data
make benchmark-dev-services-check
make benchmark-dev-services-sync

# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

The smoke run uses only the 5 development questions and is never a final
reportable result. Stream-only trigger, speculation, reuse, cancellation, and
fallback details are reported separately from metrics shared with Naive.

## Verify and operate safely

```bash
make verify-data
make check
make docker-config
```

For the tested local Docker stack, set `OPENAI_API_KEY` and
`ALLOW_UNREVIEWED_DATASET=1` in `.env`, then run `make docker-up`. From another
terminal run `make docker-sync` once and open <http://127.0.0.1:5173/>. Stop and
remove the containers with `make docker-down`. The override permits only local
development on the candidate dataset; it does not approve or unseal evaluation
data.

The Docker frontend is the only browser-facing origin. It serves `/`, `/naive`,
`/stream`, and `/compare`, and proxies `/api/naive/*` and `/api/stream/*` to the
isolated containers. A network deployment still needs TLS, authentication, rate
and spend limits, and persistent volumes; do not publish this no-auth assessment
unchanged.

The committed corpus avoids the 705 MiB upstream download. The APIs are async;
synchronous local-Qdrant work runs off the event loop. This is a bounded local
assessment, not a production multi-user service. All ports bind to loopback and
there is no authentication, authorization, rate limiting, or tenant isolation.
Do not expose the stack to a LAN or public interface. See
[`docs/SECURITY.md`](docs/SECURITY.md) and
[`docs/REAL_USER_VERIFICATION.md`](docs/REAL_USER_VERIFICATION.md).

## Attribution

The 250-document corpus is derived from Meta's
[CRAG Task 1/2 development release](https://github.com/facebookresearch/CRAG)
under **CC BY-NC 4.0**; source IDs and URLs are retained for attribution and
audit. Dependencies remain under the licenses shipped with their distributions.
