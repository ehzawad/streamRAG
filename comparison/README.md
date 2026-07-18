# Headless comparison CLI

`comparison/` provisions two isolated services, replays the same typed questions,
records content-addressed predictions, scores them offline, and renders reports.
It has no GUI and imports no application package; all runtime communication uses
HTTP/JSON/SSE.

The runner validates each API's role, capabilities, contract, fingerprints,
index health, and state identity. Gold is unavailable during inference and is
given only to the offline scorer after predictions are finalized.

## Development run

```bash
make benchmark-dev-services-check
make benchmark-dev-services-sync

# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

Provisioning builds one real-API seed index, stops it, verifies no pending
writes, and copies it into isolated Naive and Stream state. The running services
share no Qdrant, SQLite, metrics, sessions, or caches.

Development mode reads only 5 visible questions. The final CLI requires an
approved redacted inference bundle and all 10 sealed questions. Shared metrics
cover latency, reliability, automatic answer/citation proxies, usage, accounting
coverage, and observed cost; Stream scheduling diagnostics remain separate.

```bash
uv run pytest -q comparison/tests
```
