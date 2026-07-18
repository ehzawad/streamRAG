# Real-user verification

**Dataset scope:** development questions only while status is
`candidate_pending_human_review`. No unseen test row may be used for browser
acceptance or smoke verification.

## Required current-source acceptance

Verification is complete only when all three independently deployed surfaces pass:

| Surface | URL | Required behavior |
|---|---|---|
| Naive full-stack app | `http://127.0.0.1:8001` | usable question flow; no snapshot route/request; retrieval starts after Send |
| Stream full-stack app | `http://127.0.0.1:8002` | changed-only snapshots; provisional readiness before Send; no answer before Send |
| External comparison app | `http://127.0.0.1:5173` | two healthy roles; Stream-only snapshots; equal final commits; two independent answer streams |

The services must use different Qdrant directories, SQLite databases, metrics
logs, instance IDs, sessions, and cache scopes. The comparison client must reach
ports 8001 and 8002 directly; no backend may dispatch the other implementation.

Live acceptance uses a real `OPENAI_API_KEY`, real Responses calls, real
`text-embedding-3-large` embeddings, and real embedded-Qdrant search. Unit-test
stubs are useful for deterministic edge cases but cannot satisfy any live-path,
latency, correctness, cost, or reproducibility claim.

## Browser procedure

Use headed Playwright with the installed Google Chrome, then independently inspect
the result with native Computer Use.

For Naive:

1. open port 8001 and confirm the page identifies Naive;
2. type a development question and confirm there is no snapshot request;
3. click Send, observe immediate commit acceptance, answer/citation, persistence,
   and a terminal event;
4. confirm the browser console has no error.

For Stream:

1. open port 8002 and confirm the page identifies Stream;
2. type the same development question, hold the completed draft for at least
   500 ms, and confirm changed-only snapshot traffic;
3. confirm no answer appears before Send even if evidence becomes ready;
4. click Send and observe answer/citation, persistence, and a terminal event;
5. edit an earlier part of a second draft and confirm stale work is not promoted.

For comparison:

1. open port 5173 and validate both `/v1/health` identities and contract versions;
2. in Compare mode, hold the full draft before Send and confirm only port 8002
   receives snapshots;
3. confirm both answer panels still show no answer before Send;
4. click Send and verify the exact same final text is committed independently to
   ports 8001 and 8002;
5. observe separate SSE lifecycles through `answer.ready`, `answer.completed`, and
   each run terminal;
6. exercise Cancel and New turn without freezing input controls;
7. capture network evidence, a Chrome screenshot, console state, and the final
   native Computer Use view.

Browser timings are UI acceptance evidence, not the formal benchmark. The formal
runner executes paths sequentially through isolated services; the comparison UI
may commit them concurrently for a responsive side-by-side experience.

## Reproducibility procedure

Normal reproduction uses the committed 250-document corpus and does not download
the upstream 705 MiB release.

```bash
cp .env.example .env
# Add a real OPENAI_API_KEY.
make setup
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

The sync step creates one real seed index, stops it, verifies quiescence, and
clones it to isolated service stores. Reproduction evidence must record:

- elapsed setup, verification, index, run, and scoring times;
- dataset, source, configuration, and service fingerprints;
- exactly 250 documents and 1,000 physical points in each service;
- different role/instance/state identities;
- five dev questions and ten completed path outputs;
- prediction and manifest hashes;
- explicit accounting-completeness/lower-bound status.

Provider latency and first-time package downloads vary. The intended full
development reproduction remains within the user's 15–20 minute envelope, but a
measured wall time must be reported as observed evidence, not a guarantee.

## Deletion/isolation checks

Architecture tests must also prove the ownership claim:

- `comparison/` production code imports no application package;
- `shared/` imports no implementation or comparison package;
- Naive imports `shared/` only, and Stream imports `shared/` only;
- the Naive app imports and serves when Stream/comparison are unavailable;
- the Stream app imports and serves when Naive/comparison are unavailable;
- deleting the optional comparison client cannot affect either single-path UI or
  API.

## Non-blocking boundary

FastAPI and OpenAI/PydanticAI operations are async. Embedded Qdrant's synchronous
client runs on a dedicated worker, and dataset/log I/O is offloaded. The browser
keeps one changed snapshot active plus one replaceable latest draft; Send aborts
obsolete snapshot transport without waiting for it. `answer.ready` ends visible
loading before bounded persistence, while SSE stays open for final accounting and
the terminal event.

These controls support the intended local reviewer load. They are not evidence of
production-scale concurrency; that would require an authenticated deployment and
a separate HTTP/OpenAI load campaign.

## Evidence status

The current development API benchmark and index reproduction are complete for the
source and configuration hashes recorded in their manifests:

- seed provisioning completed in 48.28 seconds and produced 1,000 points from all
  250 complete documents before cloning into isolated Naive and Stream stores;
- the runner completed 5 development questions and 10 path runs in 208.13 seconds
  by shell wall clock, with a 207.796-second finalized manifest interval, zero
  path-output failures, and `complete` run integrity;
- both paths scored 100% on the fixed automatic answer/alias and support/citation
  proxies; human semantic-adjudication coverage remains 0%;
- Stream won TTFT on 5/5 pairs, with median paired TTFT delta -1,480.697 ms
  (-52.146%), paired p95 TTFT delta -960.058 ms, median total delta -1,678.759 ms,
  and median evidence lead at Send 3,053.377 ms. Every observed stabilization
  stratum favored Stream TTFT, including the one `late_stabilization` question;
- Stream used more work (17 controller attempts, 13 retrievals, and 14
  usage-accounted model calls) than Naive (5 retrievals and 5 usage-accounted
  model calls). Stream had five cancelled controller calls, one cancelled
  retrieval, two controller timeouts, and one controller failure without complete
  provider usage, so its $0.08950950 observed cost is a lower bound; no paired cost
  conclusion is valid. Naive's fully accounted observed cost was $0.05533131.

This is development-only, non-final evidence. The sealed test split was not run.
The current-source user-interface acceptance checks are complete:

| Check | Status |
|---|---|
| Headed Playwright with Google Chrome | Passed |
| Independent Chrome inspection | Passed |
| Native Computer Use inspection | Passed |

The headed Playwright pass exercised all three surfaces with real provider calls.
The Naive standalone page issued no snapshot request and returned a complete,
cited Dune answer after Send (2,680 ms TTFT; 3,109 ms total). The Stream
standalone page prepared exact evidence before Send without showing an answer and
returned the same cited answer with `precommit_exact` reuse (2,144 ms TTFT;
2,682 ms total). A rapid edit to a different draft and reversion within the
debounce window emitted no stale or redundant snapshot. Both standalone consoles
had zero warnings and errors.

On the comparison page, the final pre-Send Dune draft produced exactly one Stream
snapshot and no Naive snapshot or visible answer. Both commit requests carried
the exact same text and timestamp (`2026-07-18T20:40:03.728Z`) to the two
independent services. Stream reached first token in 1,519 ms versus 2,156 ms for
Naive and completed in 1,976 ms versus 2,555 ms. Both returned the same correct,
cited answer with saved persistence, and the console had zero warnings and
errors.

A second, context-dependent turn asked who wrote the source novel. Each path
retained its own prior session ID, both used the local retrieval tool once, and
both correctly answered Frank Herbert with the same valid citation. This harder
live diagnostic also exposed a real tail case: Stream took 6,012 ms TTFT versus
5,114 ms for Naive, so the UI does not imply a per-query speed guarantee. A mode
change then rotated both path sessions; the next pre-Send Stream snapshot used a
new session and still displayed no answer. The retained capture is
[`output/playwright/final-verified-compare.png`](../output/playwright/final-verified-compare.png).

A separate native Chrome session repeated the comparison interaction. Before
Send, the page reported `Exact draft evidence ready` while both panels still said
`No answer yet`. After Send, both panels completed with the same Denis Villeneuve
answer and three source links. In that diagnostic run, Stream TTFT was 1,783 ms
versus 3,021 ms for Naive and total time was 2,220 ms versus 3,494 ms. Native
Computer Use independently inspected the rendered Chrome window and confirmed
the two complete panels, citations, ready-before-Send indicator, saved
persistence, and displayed latency deltas.

These browser timings prove interaction and lifecycle behavior only. The external
five-question development benchmark above remains the primary measured
performance evidence, but it is permanently non-reportable and subject to its
candidate-data and zero-human-adjudication limitations.
