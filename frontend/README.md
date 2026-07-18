# Frontend

`frontend/` is a standalone React/Vite GUI for the Naive API, Stream API, or both
side by side. It owns presentation and browser request lifecycle only. It does
not import Python code, run benchmarks, read gold answers, or own either RAG
implementation.

## Run locally

Start the API or APIs you want to use, then:

```bash
make setup-frontend
make dev-frontend
```

Open <http://127.0.0.1:5173>. Draft snapshots go only to Stream. Compare mode
commits the same final text independently to both APIs.

The defaults are `http://127.0.0.1:8001` and `http://127.0.0.1:8002`. Override
them at build time with `VITE_NAIVE_API_URL` and `VITE_STREAM_API_URL`.

```bash
make check-frontend
docker build --tag typed-streamrag-frontend frontend
```

The Docker image serves static files with nginx. Public deployment also requires
public API URLs, TLS, authentication, spend controls, and matching API CORS; the
local Compose defaults are intentionally loopback-only.
