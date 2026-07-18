# Local-only security model

This is a private, single-reviewer assessment. It has no authentication and must
not be exposed to a LAN or the public internet.

| Surface | Local address |
|---|---|
| Homepage and routed UI | `127.0.0.1:5173/{,naive,stream,compare}` |
| Direct Naive developer UI/API | `127.0.0.1:8001` |
| Direct Stream developer UI/API | `127.0.0.1:8002` |

Container services may listen on `0.0.0.0` internally, but host ports remain
loopback-only. The frontend proxies same-origin `/api/naive/*` and
`/api/stream/*`; backend port numbers are not a public-browser contract. CORS
limits browser origins; it is not authentication.

## What is protected

- `OPENAI_API_KEY` stays in API process environments and is never sent to the
  browser.
- `.env`, Qdrant, SQLite, logs, metrics, and runtime state are excluded from Git
  and Docker build context.
- Naive and Stream use separate vector stores, databases, logs, sessions, caches,
  and process identities.
- Status endpoints expose reproducibility configuration and hashes, not secrets.

## What is not protected

There is no login, API token, user authorization, rate limit, quota, CSRF layer,
or tenant isolation. Anyone who can reach an API can submit or cancel turns, open
event streams, read results, spend the configured OpenAI account, and request
index sync while the service is idle.

Turn and session IDs prevent accidental collisions; they are not credentials.
`ALLOW_UNREVIEWED_DATASET` protects benchmark status, not access. Local stores and
logs can contain user text, source metadata, timing, and usage.

## Prompt and index safeguards

- Privileged instructions remain static. Questions, history, timestamps, and
  evidence are serialized as untrusted user-role content.
- `search_local_crag` is strict, read-only, local, and bounded.
- Index sync runs only while no turn or answer setup is active. It marks the
  index unavailable before mutation and ready only after checksum, source,
  version, desired-count, and physical-count checks pass.
- Every answer rechecks dataset and index identity.
- Send reserves the turn before readiness or context waits.
- Post-answer compaction, normal save, and raw fallback share one absolute
  deadline, so cancellation cannot extend the persistence lease.

## Configuration

Each process validates immutable settings at startup; changes require restart.
Python dependencies are pinned in `uv.lock` and installed with
`uv sync --frozen`. Frontend dependencies are pinned by
`frontend/package-lock.json` and installed with `npm ci`.

## Before network deployment

Add TLS, authentication and authorization, principal-bound sessions, rate and
spend limits, request-size limits, abuse monitoring, secret management, protected
index administration, access-controlled storage, retention policies, and full
concurrency/security testing. Changing the bind address alone is not a deployment
plan.
