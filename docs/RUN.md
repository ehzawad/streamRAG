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

Set `OPENAI_API_KEY=your-real-key` in `.env`.

## Reproduce the Docker development pipeline

Terminal A:

```bash
make docker-up
```

Terminal B:

```bash
make docker-sync
make benchmark
make score
```

`docker-sync` reads the committed 250-document corpus, creates 1,000 embeddings
for each path, and writes them to separate Qdrant services. Existing valid
volumes make later syncs incremental.

`make benchmark` replays `test_queries.jsonl` through both services with
deterministic 70 WPM typing, changed-only 400 ms snapshots, and a 5-second
pre-Send dwell, writing predictions to
`comparison/benchmark/results/predictions.jsonl`. `make score` binds the gold set
via `checksums.sha256` and writes `summary.json`.

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
make check
make docker-config
```

`make check` runs Python linting (ruff) and a production frontend build. The
committed corpus is checksum-bound; each service verifies those checksums when it
loads the dataset, so no separate verification step is required.

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

Start the route frontend separately with `make dev-frontend`.

The headless provisioner can also create two isolated embedded service stores:

```bash
make benchmark-services-check
make benchmark-services-sync

# terminal A
make benchmark-services-serve

# terminal B
make benchmark
make score
```

This is the single benchmark path, bound to `data/crag_eval`.

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

## Troubleshooting

- **Index is not ready:** run `make docker-sync` and inspect the API and Qdrant
  logs with `docker compose logs`.
- **Port already in use:** stop the conflicting process or the older Compose
  project before starting this stack.
- **A changed `.env` has no effect:** configuration is validated at startup;
  recreate the affected containers.

The supplied ports are loopback-only and the stack has no authentication. See
the evaluation and deployment boundary in the root README before any network
exposure.
