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

Session and turn UUIDs prevent accidental collisions; they are not authorization
credentials. `ALLOW_UNREVIEWED_DATASET` is a benchmark-integrity gate, not a
security mechanism. Local SQLite/Qdrant/metrics files may contain conversation
text, source metadata, timing, and usage, so workstation and Docker-volume file
permissions remain part of the trust boundary.

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
