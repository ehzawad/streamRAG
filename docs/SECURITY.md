# Local security and deployment boundary

This repository is a private, single-reviewer assessment application. It is safe
to run on a trusted workstation with loopback-only ports; it is **not** an
authenticated multi-user service and must not be exposed directly to a LAN or the
public internet.

## Trust boundary

- `make dev` binds FastAPI and Vite to `127.0.0.1`.
- The frontend's direct `npm run dev` and `npm run preview` defaults also bind to
  `127.0.0.1`.
- Docker Compose publishes ports 8000 and 5173 on `127.0.0.1` only. The API
  container listens on `0.0.0.0` inside its private container network because
  that is required for the loopback host mapping; it is not published on every
  host interface.
- CORS accepts only the documented localhost frontend origins. CORS is a browser
  control, **not** authentication; command-line or server clients are unaffected.

The browser never receives `OPENAI_API_KEY`. It remains in the API process
environment, `.env` is ignored by Git/Docker build context, and health/status
responses expose model configuration but not credentials.

## Intentionally absent controls

There is no login, API token, per-user authorization, rate limit, quota, CSRF
layer, or tenant isolation. Anyone who can reach the API can submit/cancel turns,
open event streams, and—while the service is idle—request index synchronization.
Those operations can consume the configured OpenAI account, read application
answers/citations, and mutate local assessment state.

Session identifiers and bounded turn identifiers prevent accidental collisions;
they are not authorization credentials. Turn IDs are pattern- and length-validated
before runtime state allocation, but remain caller-controlled. `ALLOW_UNREVIEWED_DATASET`
is a benchmark-integrity gate, not a security mechanism. Local
SQLite/Qdrant/metrics files may contain conversation text, source metadata, timing,
and usage, so workstation and Docker-volume file permissions remain part of the
trust boundary.

The grounded agent keeps privileged instructions static. The current question,
query time, conversation summary, and retrieved evidence are serialized into one
user-role JSON object and explicitly treated as untrusted data. This reduces prompt
injection privilege escalation from corpus/history text, but it is defense in depth,
not a substitute for document provenance or output review.

Index synchronization is admitted atomically only while no turn, answer task, or
commit setup is active. It marks durable metadata unready before mutation; a failed
sync remains unavailable across restart until repaired. Every answer re-verifies
dataset integrity/approval and requires source/version/count/physical-point
agreement, so a partially mutated or changed corpus fails closed. The health gate
also includes dataset approval: an otherwise valid candidate index is ready only
when the explicit development override is active.

Send atomically reserves its terminal turn record before index-readiness, context,
or event-channel awaits. The idle reaper and maintenance admission see that
reservation immediately, preventing either from overtaking an admitted commit.

Post-answer persistence has one absolute configured lease covering compaction,
normal save, and raw-save fallback. If cancellation arrives after the answer is
visible, fallback can use only the time remaining in that same lease; it cannot
start an additive emergency timeout. A stalled save therefore cannot hold the
session lease indefinitely, and the UI never reports persistence success unless
completion was observed.

## Configuration lifecycle

`app.config` loads `.env`, constructs one immutable `Settings` object, and
validates the locked benchmark configuration during process import. This is
intentional fail-fast behavior: model, embedding dimensions, reasoning roles, and
service tier cannot drift silently between paths. Environment values must be set
before process start, and changes require a restart.

Import-time construction would be a defect in a reusable library or a service
that promised hot-reloaded configuration. This executable assessment promises
neither. Components accept explicit `Settings` instances for isolated tests and
the two-service benchmark launcher starts fresh processes with their environment
set before import. The singleton is therefore a deliberate reproducibility
boundary, not dynamic application state.

## Before any network deployment

At minimum:

1. put the API behind TLS and real authentication/authorization;
2. bind sessions, turns, runs, and event streams to an authenticated principal;
3. add per-principal rate limits, model-spend quotas, request/body limits, and
   abuse monitoring;
4. disable or separately authorize `/v1/data/sync` and API documentation;
5. use a secret manager and rotate the OpenAI key;
6. replace local SQLite/Qdrant with access-controlled services and define log/data
   retention;
7. run an end-to-end concurrency, dependency, and security review.

Changing the host binding alone is not an acceptable deployment strategy.

## Reproducible dependency boundary

Python packages are locked by `uv.lock` and installed with `uv sync --frozen`.
Frontend direct dependencies are exact versions in `frontend/package.json`, the
full transitive graph is in `frontend/package-lock.json`, and setup/Docker use
`npm ci`. A clean install therefore cannot silently advance a package tagged
`latest`.
