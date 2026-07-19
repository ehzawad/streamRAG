# Naive RAG vs StreamRAG benchmark

**Status:** development evidence only. The dataset remains
`candidate_pending_human_review`, so the 10 sealed test questions and final
benchmark have not been run.

## Comparison contract

The benchmark measures one product difference: Naive starts retrieval after
**Send**; StreamRAG may prepare evidence during typed input. Both paths start
answer generation only after **Send**.

Both services use the same 250 complete documents, 1,000 chunks, embeddings,
search policy, answer model and prompt, local tool, memory policy, and offline
scorer. They run in different processes with separate Qdrant, SQLite, metrics,
sessions, and caches.

The external runner validates role, capabilities, configuration, source and
dataset fingerprints, index health, and distinct service identity. It measures
the paths sequentially and counterbalances question order. Gold is unavailable
until predictions and their manifest are finalized.

## Retained development run

Canonical artifact:
[`comparison/benchmark/results/dev-comparison/`](../comparison/benchmark/results/dev-comparison)

- 5 development questions × 2 paths;
- deterministic 70 WPM input with 400 ms changed-draft snapshots;
- 5-second pause before Send;
- real `gpt-5.6-sol` and `text-embedding-3-large` calls;
- 10/10 completed outputs and complete artifact integrity;
- 207.707-second measured runner interval.

| Path | Answer proxy | Support + citation | Median TTFT | Median total | Accounted calls | Retrievals | Observed cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 3,435.527 ms | 3,791.574 ms | 5 | 5 | $0.05536131 complete |
| StreamRAG | 100% | 100% | 1,531.129 ms | 2,340.075 ms | 18 | 15 | at least $0.10427957 |

Paired results:

- StreamRAG TTFT wins: **5/5**;
- median TTFT delta: **-967.857 ms (-41.879%)**;
- paired p95 TTFT delta: **-533.341 ms**;
- median total-time delta: **-647.562 ms**;
- exact speculative evidence reuse: **5/5**;
- median evidence lead at Send: **3,637.909 ms**.

The automatic correctness delta was zero because both paths passed all fixed
answer, support, and citation checks. Human semantic-adjudication coverage was
0%, so 100% here is not a claim of perfect semantic accuracy.

StreamRAG did more work: 19 controller attempts and 15 retrievals versus Naive's
5 retrievals. Cancelled, failed, or timed-out speculative calls do not always
return provider usage, so StreamRAG's recorded cost is a lower bound and a valid
paired cost delta is unavailable.

All five questions were faster to first token with StreamRAG in this run,
including the late-stabilizing case. The result supports a scheduling gain on
this small development set, not a guarantee that speculation helps every query.

## Real browser acceptance

The final Docker topology was also exercised through Google Chrome. Playwright
handled navigation, Send, and DOM inspection; native macOS Computer Use entered
each character in the visible textbox. No paste, programmatic fill, or whole-text
injection was used.

The same deterministic human-like cadence and verbatim prompts were used for
both paths, followed by a fixed 2.7-second pre-Send dwell. The three fresh-chat
prompts were:

1. `how long does a stock need to be held to make capital gains long term?`
2. `which dune movie has better music, 1984 or 2021?`
3. `what is the name of the bad bunny album released before nadie sabe lo que va a pasar manana?`

The four-turn conversation used prompt 1 followed by:

1. `Does exactly one year qualify?`
2. `When does that holding period start?`
3. `Summarize both rules in one sentence.`

| Scenario | Work per path | Naive avg TTFT / total | StreamRAG avg TTFT / total | StreamRAG reduction | Correct |
|---|---:|---:|---:|---:|---:|
| Standalone, fresh chat | 3 | 3,749 / 4,673 ms | 1,641.333 / 3,159.667 ms | 56.219% / 32.385% | 3/3 both |
| Simultaneous `/compare` | 3 | 1,876.333 / 2,745.333 ms | 1,307.667 / 2,316.333 ms | 30.307% / 15.627% | 3/3 both |
| Multi-turn `/compare` | 4 | 1,898.25 / 2,966.75 ms | 1,156.75 / 1,785.25 ms | 39.062% / 39.825% | 4/4 both |
| Multi-turn solo routes | 4 | 2,201.25 / 3,272 ms | 1,547.5 / 2,242.25 ms | 29.699% / 31.472% | 4/4 both |

Raw timings:

| Scenario | Naive TTFT | StreamRAG TTFT | Naive total | StreamRAG total |
|---|---|---|---|---|
| Standalone | 4,297 / 3,234 / 3,716 | 1,957 / 1,664 / 1,303 | 5,393 / 4,622 / 4,004 | 3,069 / 4,800 / 1,610 |
| Simultaneous | 1,576 / 2,048 / 2,005 | 1,799 / 919 / 1,205 | 2,778 / 3,152 / 2,306 | 2,756 / 2,660 / 1,533 |
| Multi-turn Compare | 1,844 / 1,750 / 1,821 / 2,178 | 915 / 917 / 1,110 / 1,685 | 2,614 / 4,300 / 2,355 / 2,598 | 1,610 / 1,458 / 1,877 / 2,196 |
| Multi-turn solo | 1,775 / 1,881 / 2,952 / 2,197 | 1,128 / 2,161 / 991 / 1,910 | 2,564 / 2,789 / 3,743 / 3,992 | 1,766 / 2,960 / 1,738 / 2,505 |

Both paths produced correct, cited answers on all 14 browser turns. StreamRAG had
exact evidence ready before Send on all 14, never answered early, and won 12/14
individual first-token and total-time races. Provider variance still caused two
losses; the approach does not guarantee every request is faster.

This browser study is manually reproducible product evidence, not an automated or
gold-blind benchmark. Its answers were inspected rather than hash-bound human
adjudicated.

After the final Docker rebuild on 2026-07-19, Chrome was rechecked with the Black
Swan question entered one character at a time at 120 ms intervals. Before Send,
StreamRAG showed that candidate evidence was ready while both paths still showed
no answer. After Send, both returned Natalie Portman with the same support
citation; Naive reported 2,649 ms TTFT / 3,195 ms total and StreamRAG 1,810 ms /
2,307 ms. Playwright also opened `/`, `/naive`, `/stream`, and `/compare`; all
health/data requests returned 200 and the browser console had no errors. This is
a product spot check, not an additional benchmark row.

## Clean reproduction evidence

The recorded clean Docker run verified:

- 250 documents, 5 development questions, 10 sealed questions, and all 9
  checksummed files;
- 1,000 points in each isolated Qdrant service;
- real clean index syncs of 40.926 seconds for Naive and 39.055 seconds for
  StreamRAG, each embedding 366,142 tokens;
- persistence across container removal and recreation without re-embedding;
- the then-current Python and frontend suites and a production frontend build;
- five healthy containers, matching source/index hashes, zero path failures, and
  no application error or HTTP 4xx/5xx in the final logs.

Run the same development pipeline with the commands in [`RUN.md`](RUN.md).

## Artifact ownership

`comparison/benchmark/results/dev-comparison/` contains the current development
predictions, content-addressed manifest, machine-readable summary, and Markdown
summary. They are retained because they are evidence, not generated clutter.

No final artifact is committed. The final output directory is created only after
human dataset approval and a real sealed run. It must never be filled with copied
development results or placeholders.

## Final protocol

After an explicit `approved_frozen` dataset commit, the final workflow creates a
gold-free inference bundle, starts two isolated services, replays all 10 sealed
questions once per path, and produces exactly 20 outputs. The runner rejects gold
leakage, shared identity, fingerprint drift, missing outputs, or invalid freeze
bindings. The scorer receives gold afterward; a final semantic-accuracy claim
also requires hash-bound manual adjudication.

## Claim boundary

The supported result is narrow: on this five-question development set and the
manual browser checks, StreamRAG preserved the measured correctness proxies and
reduced perceived latency when usable evidence stabilized before Send. The data
does not establish a universal speed, accuracy, or cost improvement, and results
from the StreamRAG paper are not treated as results of this implementation.
