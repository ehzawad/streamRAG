# Local security and deployment boundary

This is a private, single-reviewer assessment. Naive, Stream, and comparison
surfaces bind to loopback and are safe only on a trusted workstation. They are not
authenticated multi-user services and must not be exposed to a LAN or the public
internet.

## Network and process boundary

- Naive serves its same-origin UI/API on `127.0.0.1:8001`.
- Stream serves its same-origin UI/API on `127.0.0.1:8002`.
- The optional external comparison UI serves on `127.0.0.1:5173` and calls both
  APIs directly.
- Container processes may listen on `0.0.0.0` inside their private network, but
  host publication remains loopback-only.
- CORS permits only documented localhost origins. CORS is a browser control, not
  authentication; command-line and server clients are unaffected.

Each service has its own Qdrant directory, SQLite database, metrics log, sessions,
and cache namespace. The comparison provisioner may copy a stopped, quiescent seed
index into those directories before startup; no mutable state is shared while the
services run.

The browser never receives `OPENAI_API_KEY`. It remains in each API process
environment. `.env` and runtime state are ignored by Git and Docker build context.
Status endpoints expose reproducibility configuration and fingerprints but not
credentials.

## Intentionally absent controls

There is no login, API token, per-user authorization, rate limit, quota, CSRF
layer, or tenant isolation. Anyone who can reach an API can submit/cancel turns,
open event streams, read answers/citations, and—while idle—request index sync.
Those actions can consume the configured OpenAI account and mutate local state.

Session, turn, and run IDs prevent accidental collisions; they are not access
credentials. `ALLOW_UNREVIEWED_DATASET` is a benchmark-integrity gate, not a
security mechanism. SQLite, Qdrant, service logs, and metrics can contain user
text, source metadata, timing, and usage, so workstation/container-volume
permissions remain part of the boundary.

## Prompt and index integrity

The grounded agent keeps privileged instructions static. Question text, query
time, conversation summary, and accepted evidence are serialized as untrusted
user-role data. The strict local tool is read-only and searches only the verified
Qdrant corpus. This reduces privilege escalation from corpus/history text but does
not replace provenance or output review.

Index sync is admitted only while no turn, answer task, or commit setup is active.
It marks durable state unready before mutation; a failed operation remains
unavailable across restart. Every answer re-verifies dataset approval/checksums
and requires source/version/desired/physical-count agreement. A partially mutated
or changed corpus therefore fails closed.

Send reserves the terminal turn before awaiting index readiness, context, or
events. Post-answer compaction, normal save, and raw fallback share one absolute
persistence lease, so cancellation cannot add an unbounded emergency timeout or
report an unobserved save as successful.

## Configuration and dependencies

`shared.config` loads `.env` and validates the immutable configuration common to
both paths. Each implementation constructs its own process settings and Stream
alone validates trigger/speculation configuration. Environment values must exist
before startup, and changes require a restart. Comparison starts fresh Naive and
Stream processes with separate state variables, then verifies their common
configuration, Stream-only configuration, and distinct identities over HTTP.

Python dependencies are locked by `uv.lock` and installed with
`uv sync --frozen`. Comparison-frontend dependencies are exact in
`comparison/frontend/package.json` and transitively locked in
`comparison/frontend/package-lock.json`; setup and container builds use `npm ci`.

## Before any network deployment

At minimum:

1. put every surface behind TLS and real authentication/authorization;
2. bind sessions, turns, runs, and event streams to an authenticated principal;
3. add per-principal rate limits, spend quotas, request-size limits, and abuse
   monitoring;
4. disable or separately authorize `/v1/data/sync` and API documentation;
5. use a secret manager and rotate the OpenAI key;
6. replace local SQLite/Qdrant with access-controlled services and define data/log
   retention;
7. perform end-to-end concurrency, dependency, and security reviews.

Changing the bind address alone is not an acceptable deployment strategy.
