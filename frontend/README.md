# Frontend

`frontend/` is a React/Vite route hub and GUI. It owns presentation and browser
request lifecycle only; it does not import Python, run benchmarks, read gold, or
own either RAG implementation.

## Run locally

Start the API or APIs you want to use, then:

```bash
make setup-frontend
make dev-frontend
```

Open <http://127.0.0.1:5173/> and choose:

- `/naive` for the baseline;
- `/stream` for typed pre-retrieval;
- `/compare` for the same commit sent to both services.

Vite proxies `/api/naive/*` and `/api/stream/*` to local ports 8001 and 8002.
The Docker image applies the same contract through nginx, so deployed users need
one origin and never need to know backend ports. An isolated route probes only
its selected backend and retries readiness checks during cold starts; Compare
requires both.

Each route shows a conversation transcript and preserves its session ID across
follow-ups. **New chat** rotates the session. Compare keeps one independent
conversation per backend while presenting both answers under each user turn.

```bash
make check-frontend
docker build --tag typed-streamrag-frontend frontend
```

The Docker image serves deep links with SPA fallback and disables proxy buffering
for SSE. Public deployment still requires TLS, authentication, spend controls,
and persistent service volumes; local Compose remains loopback-only.
