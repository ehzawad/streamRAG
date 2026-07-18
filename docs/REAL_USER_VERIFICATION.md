# Real-user verification

**Scope:** development questions only while the dataset is
`candidate_pending_human_review`. The unseen test split was not used.

## UI acceptance

| Surface | URL | Expected behavior |
|---|---|---|
| Homepage | `http://127.0.0.1:5173/` | links to all three experiences |
| Naive | `http://127.0.0.1:5173/naive` | no pre-Send retrieval; answer and citations after Send |
| Stream | `http://127.0.0.1:5173/stream` | evidence may prepare while typing; no answer before Send |
| Compare | `http://127.0.0.1:5173/compare` | the same commit sent to two isolated services |

Live acceptance requires a real `OPENAI_API_KEY`, real Responses calls,
`text-embedding-3-large`, and local Qdrant. Unit-test stubs do not support live
latency, correctness, cost, or reproducibility claims.

The services must expose different implementation roles, instance IDs, Qdrant
directories, SQLite databases, logs, sessions, and cache scopes. The GUI uses
same-origin proxy paths; the CLI calls ports 8001 and 8002 directly. Neither
backend may dispatch its peer.

## Browser checklist

Use headed Playwright with installed Google Chrome, then inspect the rendered
result with native Computer Use.

1. On Naive, type a development question and confirm no snapshot request occurs.
2. On Stream, wait 500 ms after the final snapshot is delivered (about 900 ms
   after the last keystroke in the side-by-side frontend). Confirm evidence can
   become ready but the answer remains hidden.
3. In Compare mode, confirm snapshots go only to Stream and both panels remain
   answer-free before Send.
4. Press Send and verify both services receive the same text and timestamp,
   produce cited answers, persist the turn, and reach terminal SSE events.
5. Ask a referential follow-up and confirm each path uses its own prior answer as
   context. Select New chat and confirm that context and transcript both reset.
6. Correct or revert a draft and confirm stale work is not promoted or resent.
7. Exercise route changes and cancellation without freezing input.
8. Check the console, network requests, and final rendered panels.

Browser timings are interaction evidence, not the formal benchmark. The browser
starts both paths concurrently; the benchmark measures them sequentially.

## Recorded browser evidence

All routed surfaces passed in Chrome with human-speed keyboard events, real
provider calls, and the same two-turn conversation:

- **Naive:** both turns were correct and cited; the follow-up resolved to a
  short-term capital gain. TTFT was 3,475 ms then 3,433 ms.
- **Stream:** evidence was ready before Send on both turns; both answers were
  correct and cited. TTFT was 1,541 ms then 1,298 ms.
- **Compare:** both isolated histories resolved the same follow-up correctly.
  Stream was faster on the first turn (997 vs 1,939 ms TTFT) and slower on the
  second (2,397 vs 1,969 ms), showing normal provider variance rather than a
  guaranteed per-request win.
- **Direct Stream service UI:** a second referential question correctly resolved
  the 2021 Dune film and answered Hans Zimmer; both turns reused exact pre-Send
  evidence.

The homepage and all three deep links loaded through one origin. New chat removed
the transcript and rotated the active sessions. A direct Stream pre-Send reset
also accepted a new human-typed draft and prepared fresh evidence. No answer
appeared before Send.

## Reproduce the development run

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

Normal reproduction uses the committed 250-document corpus, creates one real seed
index, stops it, and clones it into two isolated stores. It does not download the
705 MiB upstream release.

Record elapsed time, hashes/fingerprints, 1,000 physical points per service, 10
completed path outputs, and whether cost accounting is complete. Provider latency
and first-time downloads vary; 15–20 minutes is a target, not a guarantee.

## Recorded benchmark evidence

- seed provisioning: 55.13 s, 250 documents, 1,000 points
- runner: 205.92 s shell wall time, 10/10 outputs, complete integrity
- both paths: 100% automatic answer/support/citation proxies; 0% human review
- Stream: 5/5 TTFT wins; median delta -784.373 ms (-40.160%)
- Naive cost: $0.05521131 complete
- Stream observed cost: at least $0.09669918; paired cost comparison invalid

Full interpretation is in [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md).

## Boundary

FastAPI and OpenAI work are async; local Qdrant work runs on a dedicated worker.
The browser keeps one active snapshot plus one replaceable latest draft and aborts
obsolete transport at Send. This is sufficient for a local reviewer, not proof of
production-scale concurrency or security.
