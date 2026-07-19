# Real-user verification

**Recorded:** 2026-07-19

**Scope:** development questions only while the dataset is
`candidate_pending_human_review`. The unseen test split was not used.

## Tested stack

The acceptance run used the real Docker Compose stack: nginx frontend, separate
Naive and Stream APIs, two isolated Qdrant server containers, and two embedded
SQLite files in persistent API volumes. Both indexes contained exactly 1,000
`text-embedding-3-large` points from the committed corpus. Answers used real
`gpt-5.6-sol` Responses API calls with medium reasoning; no live dependency was
mocked.

| Surface | URL | Expected behavior |
|---|---|---|
| Homepage | `http://127.0.0.1:5173/` | links to all three experiences |
| Naive | `http://127.0.0.1:5173/naive` | no pre-Send retrieval; cited answer after Send |
| Stream | `http://127.0.0.1:5173/stream` | evidence may prepare while typing; no answer before Send |
| Compare | `http://127.0.0.1:5173/compare` | one commit sent concurrently to two isolated services |

The Qdrant services, volumes, SQLite databases, logs, sessions, caches, process
identities, and private data networks were distinct; both APIs shared only the
frontend edge network. Each API could resolve its own Qdrant hostname but not the
peer's. The GUI used same-origin proxy paths; neither backend dispatched the
other.

## Fair human-input protocol

Google Chrome was controlled through Playwright for navigation, Send, and DOM
inspection. Native macOS Computer Use entered every character into the visible
textbox; no paste, `fill`, or whole-string injection was used.

Every compared path received verbatim text and the same deterministic schedule:

- base delay after character `i`: `48 + ((i * 37) % 58)` ms;
- spaces add `28 + ((i * 13) % 40)` ms;
- every fourth space adds a 170 ms thinking pause;
- comma, semicolon, or colon adds 210 ms; sentence punctuation adds 300 ms;
- Send follows a fixed 2,700 ms dwell after the final character.

The three standalone prompts were:

1. `how long does a stock need to be held to make capital gains long term?`
2. `which dune movie has better music, 1984 or 2021?`
3. `what is the name of the bad bunny album released before nadie sabe lo que va a pasar manana?`

The ASCII spelling `manana` was fixed before measurement and used identically
everywhere. The four-turn conversation used prompt 1 followed by:

1. `Does exactly one year qualify?`
2. `When does that holding period start?`
3. `Summarize both rules in one sentence.`

## Four recorded scenarios

Times are submit-to-first-token (TTFT) and submit-to-completion milliseconds.
Each arrow shows the Naive average, Stream average, and Stream reduction.

| Scenario | Work per path | Average TTFT | Average total | Observed correctness |
|---|---:|---:|---:|---:|
| Standalone, fresh chat | 3 questions | 3,749 → 1,641.333 (**56.219%**) | 4,673 → 3,159.667 (**32.385%**) | 3/3 both |
| Simultaneous `/compare` | 3 questions | 1,876.333 → 1,307.667 (**30.307%**) | 2,745.333 → 2,316.333 (**15.627%**) | 3/3 both |
| Multi-turn `/compare` | 4 turns | 1,898.25 → 1,156.75 (**39.062%**) | 2,966.75 → 1,785.25 (**39.825%**) | 4/4 both |
| Multi-turn solo routes | 4 turns | 2,201.25 → 1,547.5 (**29.699%**) | 3,272 → 2,242.25 (**31.472%**) | 4/4 both |

Raw timings preserve individual variance:

| Scenario | Naive TTFT | Stream TTFT | Naive total | Stream total |
|---|---|---|---|---|
| Standalone | 4,297 / 3,234 / 3,716 | 1,957 / 1,664 / 1,303 | 5,393 / 4,622 / 4,004 | 3,069 / 4,800 / 1,610 |
| Simultaneous | 1,576 / 2,048 / 2,005 | 1,799 / 919 / 1,205 | 2,778 / 3,152 / 2,306 | 2,756 / 2,660 / 1,533 |
| Multi-turn Compare | 1,844 / 1,750 / 1,821 / 2,178 | 915 / 917 / 1,110 / 1,685 | 2,614 / 4,300 / 2,355 / 2,598 | 1,610 / 1,458 / 1,877 / 2,196 |
| Multi-turn solo | 1,775 / 1,881 / 2,952 / 2,197 | 1,128 / 2,161 / 991 / 1,910 | 2,564 / 2,789 / 3,743 / 3,992 | 1,766 / 2,960 / 1,738 / 2,505 |

Both paths correctly answered the three facts and all referential follow-ups.
Compare retained separate histories, while each solo route retained its own four
turns. Stream showed exact evidence ready before Send on all 14 runs, with no
pre-Send answer and no Send fallback. Stream won 12/14 individual TTFT races and
12/14 total-time races. Its TTFT losses were the simultaneous first turn and the
solo one-year follow-up; its total-time losses were standalone Dune and that same
solo follow-up. Stream also made more controller/retrieval calls, and its displayed
cost remained a lower bound when cancelled calls lacked usage.

These are diagnostic browser observations, not the formal benchmark. The sample
is small, answers were inspected rather than hash-bound human-adjudicated, and
provider variance can dominate individual requests. The exact manual protocol is
retained above, but this is not a committed automated browser replay. The formal
runner remains the source for reportable evaluation after dataset approval.

## Clean-pipeline reproduction

The final topology was rebuilt from empty generated volumes:

- dataset verifier: 250 complete documents, 5 development questions, 10 sealed
  test questions, 1,000 deterministic points, and all 9 checksummed files valid;
- real index syncs: 47.049 s Naive and 44.990 s Stream, each embedding 1,000
  chunks and 366,142 tokens;
- ordinary `docker compose down` / `up` preserved all four named volumes and
  restored both ready indexes without re-embedding;
- `make check`: 209 Python tests, 16 frontend tests, and a production frontend
  build passed;
- controlled development evaluation through the Docker services: 10/10 path
  runs in 199.810 s, complete artifact integrity, 5/5 automatic
  correctness/support/citation checks for both paths, and 5/5 Stream TTFT wins;
  median Stream-minus-Naive TTFT was -1,024.466 ms (-44.226%) and median total
  time was -1,461.268 ms;
- live audit after the browser run: 14/14 completions per path, 0 failures,
  0 active runs, matching corpus/index hashes, and 1,000/1,000 physical points;
- all five containers were healthy; no application traceback, exception, or
  HTTP 4xx/5xx appeared in the final run logs.

Reproduce the Docker path with:

```bash
cp .env.example .env
# Add a real OPENAI_API_KEY and set ALLOW_UNREVIEWED_DATASET=1.
make setup
make verify-data
make check
make docker-up

# In another terminal, once per fresh set of volumes:
make docker-sync
make benchmark-smoke
make score-dev
```

`make docker-down` removes containers and preserves state. Only an intentional
`docker compose down --volumes` deletes the generated indexes and SQLite files.

## Boundary

Compose uses asynchronous Qdrant clients; standalone and headless embedded mode
moves synchronous Qdrant work off the event loop. SQLite is embedded in each API
container and persisted as a separate volume-backed file. This supports the
local assessment, not production-scale concurrency or public security claims.
