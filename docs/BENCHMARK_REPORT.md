# Naive RAG vs typed StreamRAG benchmark

**Status:** development evidence only. The 10-question unseen test set has not
been run because the dataset still awaits human approval.

The benchmark compares one product difference: Naive starts retrieval after
Send; Stream may retrieve while the user types. Results from the StreamRAG paper
are not treated as results of this implementation.

## Fair comparison

| Service | Endpoint | State |
|---|---|---|
| Naive | `naive.api:app`, port 8001 | own Qdrant, SQLite, metrics, sessions, caches |
| Stream | `stream.api:app`, port 8002 | own Qdrant, SQLite, metrics, sessions, caches |

Both services use the same 250 complete documents, 1,000 chunks,
`text-embedding-3-large` index, search policy, answer model/prompt/tool, memory,
and scorer. The external runner validates role, capability, fingerprints, index
readiness, and distinct process/state identity before starting.

The runner counterbalances query order and measures paths sequentially to reduce
local contention. It cannot read gold. The offline scorer receives gold only
after predictions and their manifest are finalized.

## Path behavior

- **Naive:** retrieve the exact committed question after Send.
- **Stream:** receive changed drafts every 400 ms and allow exact-draft retrieval
  after 500 ms of stability.
- **Both:** start grounded answer generation only after Send.
- **Correction safety:** Stream reuses evidence only when its recorded text
  exactly matches the commit; otherwise it takes Naive's exact-commit fallback.

The replay uses deterministic 70 WPM typing and a fixed 5 s pause before Send.
Submit-to-first-token (TTFT) is the main perceived-latency metric. Pre-Send
evidence lead is diagnostic, not automatically latency saved.

## Recorded development run

Artifact: [`comparison/benchmark/results/dev-comparison`](../comparison/benchmark/results/dev-comparison)

- 5 development questions × 2 paths
- 250 documents and 1,000 local Qdrant points
- real OpenAI answers and embeddings; no mocked live dependency
- 205.92 s shell wall time; 205.596 s finalized manifest interval
- 10/10 completed outputs; integrity status `complete`

| Path | Answer/alias proxy | Support + valid citation | Median TTFT | Median total | Accounted model calls | Retrievals | Observed cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 2,555.902 ms | 3,177.555 ms | 5 | 5 | $0.05521131 complete |
| Stream | 100% | 100% | 1,529.458 ms | 2,328.087 ms | 16 | 15 | at least $0.09669918 |

Stream won TTFT on 5/5 pairs:

- median TTFT delta: **-784.373 ms (-40.160%)**
- paired p95 TTFT delta: **-614.182 ms**
- median total-time delta: **-765.588 ms**
- speculative evidence reused: **5/5**
- median evidence lead at Send: **3,380.619 ms**

Automatic correctness deltas were zero because both paths scored 100% on the
fixed proxies. Human semantic-adjudication coverage was 0%, so this is not a
claim of perfect semantic accuracy.

Stream did more work: 17 controller attempts and 15 retrievals versus Naive's 5
retrievals. Five cancelled controller calls and one controller timeout lacked
complete provider usage.
Stream cost is therefore a lower bound, and no valid paired cost delta exists.

Five pairs are too small for a causal or universal claim. The supported product
reading is narrower: Stream preserved the measured correctness proxies and
reduced latency when intent stabilized before Send; individual questions can
still lose the race or add work.

## Reproduce the development run

```bash
make verify-data
make check
make benchmark-dev-services-check
make benchmark-dev-services-sync

# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

The seed index took 55.13 seconds in the recorded run. The runner reads only
`dev_queries.jsonl`, and the development scorer always marks its output
non-reportable.

## Final run after approval

```bash
make benchmark-inference-bundle
make benchmark-services-check
make benchmark-services-sync

# terminal A
make benchmark-services-serve

# terminal B
make benchmark
make score
```

The final runner requires exactly 10 sealed questions and 20 outputs. It refuses
candidate data, gold leakage, mismatched services or fingerprints, shared
identity, and incomplete integrity gates. A final semantic-accuracy claim also
requires hash-bound human adjudication.
