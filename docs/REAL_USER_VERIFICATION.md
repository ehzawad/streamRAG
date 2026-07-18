# Real-user verification

**Scope:** development questions only while the dataset is
`candidate_pending_human_review`. The unseen test split was not used.

## UI acceptance

| Surface | URL | Expected behavior |
|---|---|---|
| Naive | `http://127.0.0.1:8001` | no pre-Send retrieval; answer and citations after Send |
| Stream | `http://127.0.0.1:8002` | evidence may prepare while typing; no answer before Send |
| Frontend | `http://127.0.0.1:5173` | Naive, Stream, or the same commit to two isolated services |

Live acceptance requires a real `OPENAI_API_KEY`, real Responses calls,
`text-embedding-3-large`, and local Qdrant. Unit-test stubs do not support live
latency, correctness, cost, or reproducibility claims.

The services must expose different implementation roles, instance IDs, Qdrant
directories, SQLite databases, logs, sessions, and cache scopes. The GUI and CLI
call ports 8001 and 8002 directly; neither backend may dispatch its peer.

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
5. Correct or revert a draft and confirm stale work is not promoted or resent.
6. Exercise New turn, mode changes, and cancellation without freezing input.
7. Check the console, network requests, and final rendered panels.

Browser timings are interaction evidence, not the formal benchmark. The browser
starts both paths concurrently; the benchmark measures them sequentially.

## Recorded browser evidence

All three post-refactor surfaces passed with real provider calls:

- **Naive standalone:** no pre-Send answer; correct cited answer after Send;
  2,249 ms TTFT and 3,357 ms total.
- **Stream standalone:** exact evidence ready before Send, no early answer,
  `precommit_exact` reuse; 2,137 ms TTFT and 2,921 ms total.
- **Side-by-side Playwright:** both cited answers completed and persisted;
  Stream 2,152 ms TTFT and 3,296 ms total versus Naive 2,865 ms and 4,116 ms.
- **Native Chrome/Computer Use:** both panels completed with saved persistence
  and no console errors or warnings; Stream 1,145 ms TTFT and 2,341 ms total
  versus Naive 3,908 ms and 5,176 ms.

Rapid draft correction and reversion inside the debounce window emitted no stale
or redundant snapshot. Mode changes rotated both path sessions.

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
