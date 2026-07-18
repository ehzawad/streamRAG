#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

from shared.config import settings
from shared.data.index_state import IndexStateRepository
from shared.data.vector_store import QdrantVectorStore


class _EmbeddingGuard:
    model = "stored-index-vector"

    async def embed(self, texts) -> NoReturn:
        raise RuntimeError("the local Qdrant concurrency probe must not call an embedding API")


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "max": round(max(values, default=0.0), 3),
    }


def _dense_vector(value: list[float] | dict[str, list[float]] | None) -> list[float]:
    if isinstance(value, list) and value:
        return value
    if isinstance(value, dict) and len(value) == 1:
        vector = next(iter(value.values()))
        if vector:
            return vector
    raise RuntimeError("the probe requires one non-empty dense vector per sampled point")


async def _heartbeat(
    stop: asyncio.Event,
    *,
    interval_s: float,
    lags_ms: list[float],
    ready: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    target = loop.time() + interval_s
    ready.set()
    while not stop.is_set():
        await asyncio.sleep(max(0.0, target - loop.time()))
        now = loop.time()
        lags_ms.append(max(0.0, now - target) * 1000)
        target += interval_s


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if settings.qdrant_url:
        raise RuntimeError("this probe is for embedded Qdrant; unset QDRANT_URL first")
    if not args.qdrant_path.exists():
        raise RuntimeError(f"local Qdrant path does not exist: {args.qdrant_path}")

    probe_settings = replace(
        settings,
        qdrant_path=args.qdrant_path,
        runtime_db=args.runtime_db,
        qdrant_collection=args.collection,
    )
    store = QdrantVectorStore(
        probe_settings,
        _EmbeddingGuard(),
        IndexStateRepository(probe_settings.runtime_db),
    )
    try:
        info = await store.get_collection()
        points = int(info.points_count or 0)
        if points < 1:
            raise RuntimeError(f"Qdrant collection {args.collection!r} is empty")

        records, _ = await store._client_call(
            "scroll",
            collection_name=args.collection,
            limit=min(args.requests, 256),
            with_payload=False,
            with_vectors=True,
        )
        vectors = [_dense_vector(record.vector) for record in records]
        if not vectors:
            raise RuntimeError(f"Qdrant collection {args.collection!r} has no stored vectors")

        async def query(vector: list[float]) -> tuple[float, int]:
            started = time.perf_counter()
            response = await store._client_call(
                "query_points",
                collection_name=args.collection,
                query=vector,
                limit=args.limit,
                with_payload=True,
                with_vectors=False,
            )
            return (time.perf_counter() - started) * 1000, len(response.points)

        for index in range(args.warmup):
            _, hit_count = await query(vectors[index % len(vectors)])
            if hit_count < 1:
                raise RuntimeError("Qdrant returned no results during warm-up")

        semaphore = asyncio.Semaphore(args.concurrency)
        request_latencies_ms: list[float] = []
        errors: list[str] = []

        async def bounded_query(index: int) -> None:
            async with semaphore:
                try:
                    latency_ms, hit_count = await query(vectors[index % len(vectors)])
                    request_latencies_ms.append(latency_ms)
                    if hit_count < 1:
                        errors.append(f"request {index} returned no hits")
                except Exception as exc:  # noqa: BLE001 - collect every load-probe failure
                    errors.append(f"request {index}: {type(exc).__name__}: {exc}")

        stop = asyncio.Event()
        ready = asyncio.Event()
        event_loop_lags_ms: list[float] = []
        pulse = asyncio.create_task(
            _heartbeat(
                stop,
                interval_s=args.heartbeat_ms / 1000,
                lags_ms=event_loop_lags_ms,
                ready=ready,
            )
        )
        await ready.wait()
        started = time.perf_counter()
        await asyncio.gather(*(bounded_query(index) for index in range(args.requests)))
        wall_ms = (time.perf_counter() - started) * 1000
        stop.set()
        await pulse

        max_event_loop_lag_ms = max(event_loop_lags_ms, default=0.0)
        passed = (
            not errors
            and len(request_latencies_ms) == args.requests
            and max_event_loop_lag_ms <= args.max_event_loop_lag_ms
        )
        return {
            "probe": "embedded_qdrant_concurrency",
            "collection": args.collection,
            "points": points,
            "vector_dimensions": len(vectors[0]),
            "requests": args.requests,
            "concurrency": args.concurrency,
            "warmup_requests": args.warmup,
            "top_k": args.limit,
            "wall_ms": round(wall_ms, 3),
            "throughput_requests_per_s": round(args.requests / (wall_ms / 1000), 3),
            "request_latency_ms": _distribution(request_latencies_ms),
            "event_loop_lag_ms": _distribution(event_loop_lags_ms),
            "event_loop_lag_budget_ms": args.max_event_loop_lag_ms,
            "errors": errors,
            "passed": passed,
            "scope": (
                "Real embedded-Qdrant ANN and payload reads through the application's "
                "dedicated worker; stored vectors intentionally exclude OpenAI latency."
            ),
            "limitations": [
                "Request latency includes queueing on the single serialized local-Qdrant worker.",
                "This isolates vector search; it is not an end-to-end HTTP or OpenAI load test.",
                "Results characterize this corpus and machine, not an unbounded traffic guarantee.",
            ],
        }
    finally:
        await store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe real embedded-Qdrant concurrency and asyncio event-loop lag"
    )
    parser.add_argument("--qdrant-path", type=Path, default=settings.qdrant_path)
    parser.add_argument("--runtime-db", type=Path, default=settings.runtime_db)
    parser.add_argument("--collection", default=settings.qdrant_collection)
    parser.add_argument("--requests", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--limit", type=int, default=settings.retrieve_candidates)
    parser.add_argument("--heartbeat-ms", type=float, default=5.0)
    parser.add_argument("--max-event-loop-lag-ms", type=float, default=50.0)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("--requests must be positive")
    if not 1 <= args.concurrency <= args.requests:
        parser.error("--concurrency must be between 1 and --requests")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.heartbeat_ms <= 0 or args.max_event_loop_lag_ms <= 0:
        parser.error("heartbeat interval and lag budget must be positive")
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - render a concise CLI failure
        parser.exit(2, f"probe failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
