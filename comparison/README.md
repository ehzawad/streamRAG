# Comparison CLI

`comparison/` is the headless benchmark consumer. It provisions isolated Naive
RAG and StreamRAG services, replays identical typed questions, records
content-addressed predictions, and scores them offline. It has no GUI and
communicates only over HTTP/JSON/SSE.

The runner checks each service's role, contract, fingerprints, index health, and
state identity. Gold answers are unavailable during inference and reach only the
offline scorer after predictions are finalized.

## Development run

```bash
make benchmark-dev-services-check
make benchmark-dev-services-sync

# Terminal A
make benchmark-dev-services-serve

# Terminal B
make benchmark-smoke
make score-dev
```

The committed development report is in
`benchmark/results/dev-comparison/`. There is no committed `final/` result yet:
the sealed test run is allowed only after human dataset approval, and the final
directory is created by that real run.

Development mode uses five visible questions. Final evaluation requires an
approved redacted inference bundle and all ten sealed questions. The report
compares common latency, correctness, reliability, usage, and cost metrics;
StreamRAG scheduling diagnostics remain path-specific.

```bash
uv run pytest -q comparison/tests
```
