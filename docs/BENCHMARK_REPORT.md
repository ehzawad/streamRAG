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
- 199.810 s finalized manifest interval
- 10/10 completed outputs; integrity status `complete`

| Path | Answer/alias proxy | Support + valid citation | Median TTFT | Median total | Accounted model calls | Retrievals | Observed cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 2,160.747 ms | 3,588.150 ms | 5 | 5 | $0.05497131 complete |
| Stream | 100% | 100% | 1,229.992 ms | 2,137.781 ms | 17 | 15 | at least $0.09963827 |

Stream won TTFT on 5/5 pairs:

- median TTFT delta: **-1,024.466 ms (-44.226%)**
- paired p95 TTFT delta: **-562.301 ms**
- median total-time delta: **-1,461.268 ms**
- speculative evidence reused: **5/5**
- median evidence lead at Send: **3,427.312 ms**

Automatic correctness deltas were zero because both paths scored 100% on the
fixed proxies. Human semantic-adjudication coverage was 0%, so this is not a
claim of perfect semantic accuracy.

Stream did more work: 19 controller attempts and 15 retrievals versus Naive's 5
retrievals. Cancelled, failed, or timed-out speculative work did not always
return provider usage.
Stream cost is therefore a lower bound, and no valid paired cost delta exists.

Five pairs are too small for a causal or universal claim. The supported product
reading is narrower: Stream preserved the measured correctness proxies and
reduced latency when intent stabilized before Send; individual questions can
still lose the race or add work.

## Interactive Docker acceptance

A separate Chrome acceptance run exercised the final Compose topology with two
Qdrant server containers. Native Computer Use entered the same text character by
character with a fixed cadence and 2.7 s pre-Send dwell; Playwright inspected the
rendered answers and telemetry. This is product evidence, not an extension of the
formal five-question benchmark.

| Scenario | Work per path | Naive avg TTFT / total | Stream avg TTFT / total | Stream reduction | Correct |
|---|---:|---:|---:|---:|---:|
| Standalone, fresh chat | 3 | 3,749 / 4,673 ms | 1,641.333 / 3,159.667 ms | 56.219% / 32.385% | 3/3 both |
| Simultaneous `/compare` | 3 | 1,876.333 / 2,745.333 ms | 1,307.667 / 2,316.333 ms | 30.307% / 15.627% | 3/3 both |
| Multi-turn `/compare` | 4 | 1,898.25 / 2,966.75 ms | 1,156.75 / 1,785.25 ms | 39.062% / 39.825% | 4/4 both |
| Multi-turn solo routes | 4 | 2,201.25 / 3,272 ms | 1,547.5 / 2,242.25 ms | 29.699% / 31.472% | 4/4 both |

Stream won 12/14 individual TTFT races, not all of them. Both paths produced
14/14 contextually correct, cited answers; Stream had exact evidence ready before
Send on all 14 turns and never answered early. The exact prompts, per-turn
timings, typing schedule, pipeline checks, and claim limits are in
[`REAL_USER_VERIFICATION.md`](REAL_USER_VERIFICATION.md).

The final clean-pipeline check also reran all five development questions through
these Docker services: 10/10 path runs completed with intact artifacts and 5/5
automatic correctness/support/citation checks for both paths. Stream won all five
TTFT pairs; median Stream-minus-Naive TTFT was -1,024.466 ms (-44.226%) and
median total time was -1,461.268 ms. Cost remained a Stream lower bound because
cancelled speculative calls can lack provider usage.

## Reproduce the development run

```bash
cp .env.example .env
# Add a real OPENAI_API_KEY and set ALLOW_UNREVIEWED_DATASET=1.
make setup
make verify-data
make check

# terminal A
make docker-up

# terminal B
make docker-sync
make benchmark-smoke
make score-dev
```

The two clean Docker syncs took 47.049 and 44.990 seconds in the recorded run.
The runner reads only `dev_queries.jsonl`, and the development scorer always
marks its output non-reportable.

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
