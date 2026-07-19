# Run and reproduce

## Prerequisites

For the Docker application:

- Docker Desktop or Docker Engine with Compose;
- `make` and `curl`;
- a real OpenAI API key.

For local tests and standalone services, also install `uv`, Python 3.14, and
Node.js/npm.

## Configure

```bash
cp .env.example .env
```

Set these values in `.env`:

```dotenv
OPENAI_API_KEY=your-real-key
ALLOW_UNREVIEWED_DATASET=1
```

The override permits development work on the pending candidate. It does not
approve the dataset or authorize the sealed test run.

## Reproduce the Docker development pipeline

Terminal A:

```bash
make docker-up
```

Terminal B:

```bash
make docker-sync
make benchmark-smoke
make score-dev
```

`docker-sync` reads the committed 250-document corpus, creates 1,000 embeddings
for each path, and writes them to separate Qdrant services. On the recorded clean
run, Naive indexed in 40.926 seconds and StreamRAG in 39.055 seconds. Existing
valid volumes make later syncs incremental.

`benchmark-smoke` replays the five visible development questions through both
services with deterministic 70 WPM typing, changed-only 400 ms snapshots, a
5-second pre-Send dwell, and one measured repetition. `score-dev` verifies and
updates the retained non-final artifact at
`comparison/benchmark/results/dev-comparison/`.

Open <http://127.0.0.1:5173/> after both indexes are ready:

| URL | Experience |
|---|---|
| <http://127.0.0.1:5173/naive> | Naive RAG only |
| <http://127.0.0.1:5173/stream> | StreamRAG only |
| <http://127.0.0.1:5173/compare> | simultaneous comparison |

Ports 8001 and 8002 expose JSON APIs and generated OpenAPI documentation. End
users need only the frontend URL.

## Verify the repository

```bash
make setup
make verify-data
make check
make docker-config
```

`make check` runs Python linting and tests, frontend tests, and a production
frontend build. `make verify-data` validates the checksum-bound corpus and the
deterministic 1,000-point target without calling OpenAI.

Useful live checks:

```bash
docker compose ps
curl --fail http://127.0.0.1:8001/v1/health
curl --fail http://127.0.0.1:8002/v1/health
```

Both API health payloads should report `"ok": true`, `"index_ready": true`, and
`"indexed_chunks": 1000`.

## Run components separately

The two products do not require the frontend or comparison runner:

```bash
make dev-naive   # port 8001, embedded local Qdrant
make dev-stream  # port 8002, embedded local Qdrant
```

Start the route frontend separately with `make dev-frontend`. Exact component
commands and focused tests are in:

- [`naive/README.md`](../naive/README.md)
- [`stream/README.md`](../stream/README.md)
- [`frontend/README.md`](../frontend/README.md)
- [`comparison/README.md`](../comparison/README.md)

The headless provisioner can also create two isolated embedded service stores:

```bash
make benchmark-dev-services-check
make benchmark-dev-services-sync

# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

## Persistent state

Compose uses a stable lowercase project name, `streamrag`, and four named volumes:

- `streamrag_naive-qdrant`
- `streamrag_naive-runtime`
- `streamrag_stream-qdrant`
- `streamrag_stream-runtime`

`make docker-down` removes containers and networks but preserves these volumes.
To deliberately rebuild generated state from the committed corpus:

```bash
docker compose down --volumes
make docker-up
# Then rerun make docker-sync from another terminal.
```

Deleting these volumes removes generated indexes, SQLite conversations, and logs;
it does not remove the committed dataset.

## Final evaluation after approval

The final result is intentionally absent while the dataset status is
`candidate_pending_human_review`. No sealed question has been run. After an
explicit human review and checksum-bound `approved_frozen` commit:

```bash
make benchmark-inference-bundle
make benchmark-services-check
make benchmark-services-sync

# terminal A
make benchmark-services-serve

# terminal B
make benchmark
make score-final
```

The commands create `comparison/benchmark/results/final/` only when a real final
run exists. That directory should not contain placeholders or development data.
The final runner requires exactly 10 sealed questions and 20 complete path
outputs; `score-final` additionally requires hash-bound manual adjudications.

## Troubleshooting

- **API health says the dataset is unapproved:** set
  `ALLOW_UNREVIEWED_DATASET=1` only for the development run, then recreate the
  containers.
- **Index is not ready:** run `make docker-sync` and inspect the API and Qdrant
  logs with `docker compose logs`.
- **Port already in use:** stop the conflicting process or the older Compose
  project before starting this stack.
- **A changed `.env` has no effect:** configuration is validated at startup;
  recreate the affected containers.

The supplied ports are loopback-only and the stack has no authentication. See
the deployment and security boundary in [`PIPELINE.md`](PIPELINE.md) before any
network exposure.
