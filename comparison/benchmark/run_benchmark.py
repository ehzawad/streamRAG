#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from comparison.benchmark.typed_trace import (
    SNAPSHOT_INTERVAL_MS,
    cumulative_typed_trace,
    typing_duration_ms,
)
from comparison.contracts import COMMON_IDENTITY_FIELDS, service_identity_issues

ROOT = Path(__file__).resolve().parents[2]
FREEZE_DOMAIN = b"typed-streamrag-eval-freeze-v1\0"
DEFAULT_INFERENCE_DIR = ROOT / "comparison" / "benchmark" / "results" / "inference_bundle"
SETTLED_DRAFT_DELAY_MS = 500


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def opaque_freeze_id(evaluation_manifest_sha256: str) -> str:
    return hashlib.sha256(FREEZE_DOMAIN + evaluation_manifest_sha256.encode("ascii")).hexdigest()


def manifest_relative_path(target: Path, manifest_path: Path) -> str:
    """Store a portable path relative to the adjacent run manifest."""

    return Path(os.path.relpath(target.resolve(), start=manifest_path.parent.resolve())).as_posix()


def session_scope(run_tag: str, repetition: int, row_id: str, path: str) -> str:
    return f"bench-{run_tag}-{repetition}-{row_id}-{path}"


def exact_final_snapshot_completed(schedules: list[dict[str, Any]], query: str) -> bool:
    """Require exactly one delivered full-draft snapshot before Send."""

    query_length = len(query.strip())
    exact_snapshots: list[dict[str, Any]] = []
    for schedule in schedules:
        try:
            character_count = int(schedule.get("character_count") or -1)
        except TypeError, ValueError:
            continue
        if schedule.get("is_final") is True and character_count == query_length:
            exact_snapshots.append(schedule)
    return len(exact_snapshots) == 1 and exact_snapshots[0].get("transport_status") == "completed"


def settled_final_snapshot_observed(
    schedules: list[dict[str, Any]],
    events: list[dict[str, Any]],
    query: str,
    commit_perf_ms: float,
) -> bool:
    """Require the quiet-period worker to process the delivered full draft before Send."""
    expected_query = query.strip()
    query_length = len(expected_query)
    revisions = {
        int(schedule["revision"])
        for schedule in schedules
        if schedule.get("is_final") is True
        and schedule.get("transport_status") == "completed"
        and int(schedule.get("character_count") or -1) == query_length
    }
    if len(revisions) != 1:
        return False
    revision = next(iter(revisions))

    def arrived_before_send(event: dict[str, Any]) -> bool:
        if event.get("benchmark_received_perf_ms") is not None:
            return float(event["benchmark_received_perf_ms"]) <= commit_perf_ms
        return (
            event.get("benchmark_offset_from_commit_ms") is not None
            and float(event["benchmark_offset_from_commit_ms"]) <= 0
        )

    settled = any(
        event.get("type") == "draft.settled"
        and int(event.get("revision") or -1) == revision
        and event.get("query") == expected_query
        and event.get("state") in {"starting", "in_flight", "ready"}
        and arrived_before_send(event)
        for event in events
    )
    retrieval_started = any(
        event.get("type") == "retrieval.started"
        and int(event.get("revision") or -1) == revision
        and event.get("query") == expected_query
        and arrived_before_send(event)
        for event in events
    )
    return settled and retrieval_started


def read_checksum_manifest(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, name = line.split(maxsplit=1)
        except ValueError as exc:
            raise ValueError(f"invalid checksum manifest line {line_number}") from exc
        name = name.lstrip("*").strip()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid SHA-256 at checksum manifest line {line_number}")
        if name in checksums:
            raise ValueError(f"duplicate checksum manifest entry: {name}")
        checksums[name] = digest
    return checksums


async def sse_events(client: httpx.AsyncClient, url: str, source: str):
    event_name = "message"
    async with client.stream("GET", url, timeout=None) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                payload.setdefault("type", event_name)
                payload["benchmark_event_source"] = source
                payload["benchmark_received_perf_ms"] = time.perf_counter() * 1000
                yield payload
            elif not line:
                event_name = "message"


async def collect_sse(
    client: httpx.AsyncClient,
    url: str,
    source: str,
    destination: list[dict[str, Any]],
) -> None:
    async for event in sse_events(client, url, source):
        destination.append(event)


async def collect_sse_after(
    ready: asyncio.Event,
    client: httpx.AsyncClient,
    url: str,
    source: str,
    destination: list[dict[str, Any]],
) -> None:
    """Subscribe only after the first snapshot has created the turn channel."""
    await ready.wait()
    await collect_sse(client, url, source, destination)


async def send_snapshot(
    client: httpx.AsyncClient,
    transport_lock: asyncio.Lock,
    base_url: str,
    turn_id: str,
    session_id: str,
    revision: int,
    text_value: str,
    case_started_ms: float,
    schedule: dict[str, Any],
    turn_ready: asyncio.Event,
) -> None:
    try:
        async with transport_lock:
            sent_ms = time.perf_counter() * 1000
            schedule["sent_offset_ms"] = round(sent_ms - case_started_ms, 3)
            response = await client.post(
                f"{base_url}/v1/turns/{turn_id}/snapshots",
                json={
                    "session_id": session_id,
                    "revision": revision,
                    "text": text_value,
                },
            )
            response.raise_for_status()
            turn_ready.set()
            schedule["completed_offset_ms"] = round(time.perf_counter() * 1000 - case_started_ms, 3)
            schedule["transport_status"] = "completed"
    except asyncio.CancelledError:
        if schedule.get("transport_status") == "scheduled":
            schedule["transport_status"] = "cancelled_case_cleanup"
        raise
    except Exception as exc:
        schedule["completed_offset_ms"] = round(time.perf_counter() * 1000 - case_started_ms, 3)
        schedule["transport_status"] = "error"
        schedule["transport_error"] = f"{type(exc).__name__}: {exc}"


def _nested_bool(event: dict[str, Any], key: str) -> bool | None:
    value = event.get(key)
    if isinstance(value, bool):
        return value
    for nested_key in ("retrieval", "diagnostics", "stream"):
        nested = event.get(nested_key)
        if isinstance(nested, dict) and isinstance(nested.get(key), bool):
            return nested[key]
    return None


def classify_diagnostics(
    *,
    path: str,
    events: list[dict[str, Any]],
    commit_perf_ms: float,
    timing: dict[str, Any],
) -> dict[str, Any]:
    event_types = [str(event.get("type", "message")) for event in events]
    retrieval_events = [event for event in events if event.get("type") == "retrieval.ready"]
    retrieval_started = [event for event in events if event.get("type") == "retrieval.started"]
    completion = next(
        (event for event in reversed(events) if event.get("type") == "answer.completed"),
        {},
    )
    retrieval_summary = completion.get("retrieval")
    retrieval_summary = retrieval_summary if isinstance(retrieval_summary, dict) else {}
    reuse_summary = completion.get("reuse")
    reuse_summary = reuse_summary if isinstance(reuse_summary, dict) else {}
    cache_observations = [
        observation
        for event in events
        if (observation := _nested_bool(event, "cache_hit")) is not None
    ]
    precommit_ready = [
        event
        for event in retrieval_events
        if float(event.get("benchmark_received_perf_ms", commit_perf_ms)) < commit_perf_ms
    ]
    postcommit_started = [
        event
        for event in retrieval_started
        if float(event.get("benchmark_received_perf_ms", commit_perf_ms)) >= commit_perf_ms
    ]
    lead_ms = float(timing.get("accepted_retrieval_lead_at_commit_ms") or 0.0)
    accepted_ready_before_commit = retrieval_summary.get("accepted_ready_before_commit") is True
    accepted_from_fallback = retrieval_summary.get("accepted_from_fallback") is True
    reuse_mode = str(reuse_summary.get("mode") or "unknown")
    explicit_fallback = any(
        "fallback" in event_type or "commit_retrieval" in event_type for event_type in event_types
    )

    if path == "naive":
        evidence_stage = "post_submit"
    elif accepted_ready_before_commit or lead_ms > 0:
        evidence_stage = "presubmit_reuse"
    elif reuse_mode == "presubmit_retrieval_revalidated_at_commit":
        evidence_stage = "presubmit_revalidated_at_commit"
    elif reuse_mode == "inflight_completed_postcommit":
        evidence_stage = "inflight_postcommit_overlap"
    elif accepted_from_fallback or reuse_mode == "committed_text_retrieval" or explicit_fallback:
        evidence_stage = "committed_text_retrieval"
    else:
        evidence_stage = "commit_or_unknown"

    if not cache_observations:
        cache_status = "unknown"
    elif all(cache_observations):
        cache_status = "hit"
    elif any(cache_observations):
        cache_status = "mixed"
    else:
        cache_status = "miss"

    return {
        "evidence_stage": evidence_stage,
        "speculative_reuse": evidence_stage
        in {"presubmit_reuse", "presubmit_revalidated_at_commit"},
        "inflight_postcommit_overlap": evidence_stage == "inflight_postcommit_overlap",
        "commit_fallback": path == "stream" and evidence_stage == "committed_text_retrieval",
        "reuse_mode": reuse_mode,
        "accepted_ready_before_commit": accepted_ready_before_commit,
        "accepted_retrieval_lead_at_commit_ms": lead_ms,
        "accepted_candidate_retrieval_lead_ms": float(
            timing.get("accepted_candidate_retrieval_lead_ms") or 0.0
        ),
        "cache_status": cache_status,
        "cache_hits": sum(cache_observations),
        "cache_observations": len(cache_observations),
        "trigger_decisions": event_types.count("trigger.decision"),
        "trigger_errors": event_types.count("trigger.error"),
        "settled_drafts": event_types.count("draft.settled"),
        "retrieval_started": len(retrieval_started),
        "retrieval_ready": len(retrieval_events),
        "retrieval_discarded": event_types.count("retrieval.discarded"),
        "presubmit_retrieval_ready": len(precommit_ready),
        "postcommit_retrieval_started": len(postcommit_started),
    }


async def replay_path(
    client: httpx.AsyncClient,
    base_url: str,
    row: dict[str, Any],
    repetition: int,
    words_per_minute: float,
    post_typing_dwell_ms: float,
    path: str,
    path_order_position: int,
    run_tag: str,
    max_typing_drift_ms: float,
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    turn_id = str(uuid.uuid4())
    # Runtime cache and memory scopes derive from session_id. A unique scope per
    # path/query/repetition prevents either A/B path from receiving the other's work.
    session_id = session_scope(run_tag, repetition, str(row["id"]), path)
    words = row["query"].split()
    typed_trace = cumulative_typed_trace(
        row["query"],
        words_per_minute,
        post_typing_dwell_ms=post_typing_dwell_ms,
    )
    simulated_typing_ms = typing_duration_ms(row["query"], words_per_minute)
    planned_commit_offset_ms = simulated_typing_ms + post_typing_dwell_ms
    revision = 0
    case_started_ms = time.perf_counter() * 1000
    turn_events: list[dict[str, Any]] = []
    run_events: list[dict[str, Any]] = []
    snapshot_schedule: list[dict[str, Any]] = []
    snapshot_tasks: list[asyncio.Task[None]] = []
    snapshot_transport_lock = asyncio.Lock()
    turn_ready = asyncio.Event()
    turn_collector = (
        asyncio.create_task(
            collect_sse_after(
                turn_ready,
                client,
                f"{base_url}/v1/turns/{turn_id}/events",
                "turn",
                turn_events,
            )
        )
        if path == "stream"
        else None
    )
    await asyncio.sleep(0)

    commit_perf_ms = 0.0
    case_completed = False
    try:
        for snapshot in typed_trace:
            target_ms = case_started_ms + snapshot.planned_offset_ms
            remaining_s = max(0.0, target_ms - time.perf_counter() * 1000) / 1000
            if remaining_s:
                await asyncio.sleep(remaining_s)
            if path == "stream":
                revision += 1
                schedule = {
                    "revision": revision,
                    "character_count": snapshot.character_count,
                    "word_count": snapshot.word_count,
                    "is_final": snapshot.is_final,
                    "planned_offset_ms": round(snapshot.planned_offset_ms, 3),
                    "scheduled_offset_ms": round(time.perf_counter() * 1000 - case_started_ms, 3),
                    "transport_status": "scheduled",
                }
                snapshot_schedule.append(schedule)
                snapshot_tasks.append(
                    asyncio.create_task(
                        send_snapshot(
                            client,
                            snapshot_transport_lock,
                            base_url,
                            turn_id,
                            session_id,
                            revision,
                            snapshot.text,
                            case_started_ms,
                            schedule,
                            turn_ready,
                        )
                    )
                )
        send_target_ms = case_started_ms + planned_commit_offset_ms
        remaining_s = max(0.0, send_target_ms - time.perf_counter() * 1000) / 1000
        if remaining_s:
            await asyncio.sleep(remaining_s)
        # Send carries the full text as a higher revision. A full-text dirty snapshot
        # may already exist after the declared pause, but it cannot commit or answer.
        revision += 1

        send_boundary_perf_ms = time.perf_counter() * 1000
        # Match the browser: Send invalidates queued/in-flight snapshots immediately.
        # Cancellation is issued without awaiting transport cleanup, so Stream does
        # not receive artificial typing headroom.
        for task, schedule in zip(snapshot_tasks, snapshot_schedule, strict=True):
            if not task.done():
                schedule["transport_status"] = "aborted_at_commit"
                task.cancel()
        commit_perf_ms = time.perf_counter() * 1000
        actual_commit_offset_ms = commit_perf_ms - case_started_ms
        typing_drift_ms = actual_commit_offset_ms - planned_commit_offset_ms
        committed = await client.post(
            f"{base_url}/v1/turns/{turn_id}/commit",
            json={
                "session_id": session_id,
                "revision": revision,
                "text": row["query"],
                "query_time": row["query_time"],
            },
        )
        committed.raise_for_status()
        committed_payload = committed.json()
        if committed_payload.get("path") != path:
            raise RuntimeError(
                f"{path} service returned {committed_payload.get('path')!r} implementation"
            )
        events_url = committed_payload["events_url"]
        output: dict[str, Any] | None = None
        answer_started_sources: list[dict[str, Any]] = []
        async for event in sse_events(client, f"{base_url}{events_url}", "run"):
            run_events.append(event)
            if event["type"] == "answer.started":
                answer_started_sources = list(event.get("sources") or [])
            elif event["type"] == "answer.completed":
                output = {
                    "schema_version": 3,
                    "id": row["id"],
                    "repetition": repetition,
                    "path": event["path"],
                    "path_order_position": path_order_position,
                    "query": row["query"],
                    "query_time": row["query_time"],
                    "domain": row.get("domain"),
                    "question_type": row.get("question_type"),
                    "dynamism": row.get("dynamism"),
                    "stabilization_class": row.get("stabilization_class", "unreviewed"),
                    "stabilization_confidence": row.get("stabilization_confidence", "unreviewed"),
                    "stabilization_reason": row.get("stabilization_reason", ""),
                    "stabilization_review_status": row.get(
                        "stabilization_review_status", "unreviewed"
                    ),
                    "word_count": len(words),
                    "answer": event["answer"],
                    "sources": event.get("sources") or answer_started_sources,
                    "timing": event["timing"],
                    "usage": event["usage"],
                    "estimated_cost_usd": event["estimated_cost_usd"],
                    "retrieval": event.get("retrieval", {}),
                    "controller": event.get("controller", {}),
                    "reuse": event.get("reuse", {}),
                    "tool_traces": event.get("tool_traces", []),
                    "stream": event.get("stream", {}),
                    "service_base_url": base_url,
                    "session_scope": session_id,
                    "typing": {
                        "words_per_minute": words_per_minute,
                        "snapshot_interval_ms": SNAPSHOT_INTERVAL_MS,
                        "settled_draft_delay_ms": SETTLED_DRAFT_DELAY_MS,
                        "typing_duration_ms": simulated_typing_ms,
                        "post_typing_dwell_ms": post_typing_dwell_ms,
                        "simulated_duration_ms": planned_commit_offset_ms,
                        "snapshot_count": len(snapshot_schedule),
                        "planned_commit_offset_ms": planned_commit_offset_ms,
                        "send_boundary_offset_ms": send_boundary_perf_ms - case_started_ms,
                        "actual_commit_offset_ms": actual_commit_offset_ms,
                        "snapshot_abort_overhead_ms": commit_perf_ms - send_boundary_perf_ms,
                        "commit_drift_ms": typing_drift_ms,
                        "max_allowed_drift_ms": max_typing_drift_ms,
                        "drift_within_tolerance": abs(typing_drift_ms) <= max_typing_drift_ms,
                    },
                    "post_commit_wall_ms": time.perf_counter() * 1000 - commit_perf_ms,
                    "simulation_wall_ms": time.perf_counter() * 1000 - case_started_ms,
                }
            elif event["type"] == "run.error":
                raise RuntimeError(f"run failed for {row['id']}: {event.get('message')}")
        if output is None or output["path"] != path:
            raise RuntimeError(f"missing {path} output for {row['id']}")
        case_completed = True
    finally:
        if snapshot_tasks:
            if not case_completed:
                for task, schedule in zip(snapshot_tasks, snapshot_schedule, strict=True):
                    if not task.done():
                        schedule["transport_status"] = "cancelled_case_cleanup"
                        task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*snapshot_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except TimeoutError:
                for task in snapshot_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*snapshot_tasks, return_exceptions=True)
        if turn_collector is not None:
            if not turn_ready.is_set():
                turn_collector.cancel()
            else:
                _, pending = await asyncio.wait({turn_collector}, timeout=2.0)
                for task in pending:
                    task.cancel()
            await asyncio.gather(turn_collector, return_exceptions=True)
        if not case_completed:
            cleanup_task = asyncio.create_task(client.delete(f"{base_url}/v1/turns/{turn_id}"))
            try:
                cleanup_response = await asyncio.shield(asyncio.wait_for(cleanup_task, timeout=2.0))
                if cleanup_response.status_code not in {204, 404}:
                    cleanup_response.raise_for_status()
                lifecycle["turn_cleanup_status"] = "completed"
            except Exception as exc:
                lifecycle["turn_cleanup_status"] = "error"
                lifecycle["turn_cleanup_error"] = f"{type(exc).__name__}: {exc}"
                if not cleanup_task.done():
                    cleanup_task.cancel()
                await asyncio.gather(cleanup_task, return_exceptions=True)
        else:
            lifecycle["turn_cleanup_status"] = "not_required"

    all_events = sorted(
        [*turn_events, *run_events],
        key=lambda event: float(event.get("benchmark_received_perf_ms", 0)),
    )
    output["diagnostics"] = classify_diagnostics(
        path=path,
        events=all_events,
        commit_perf_ms=commit_perf_ms,
        timing=output["timing"],
    )
    for event in all_events:
        received_ms = float(event.pop("benchmark_received_perf_ms", commit_perf_ms))
        event["benchmark_offset_from_commit_ms"] = round(received_ms - commit_perf_ms, 3)
    output["snapshot_schedule"] = snapshot_schedule
    snapshot_transport_errors = sum(
        schedule.get("transport_status") not in {"completed", "aborted_at_commit"}
        for schedule in snapshot_schedule
    )
    exact_final_completed = path != "stream" or exact_final_snapshot_completed(
        snapshot_schedule, str(row["query"])
    )
    settled_final_observed = path != "stream" or settled_final_snapshot_observed(
        snapshot_schedule,
        all_events,
        str(row["query"]),
        commit_perf_ms,
    )
    output["exact_final_snapshot_completed"] = exact_final_completed if path == "stream" else None
    output["settled_final_snapshot_observed"] = settled_final_observed if path == "stream" else None
    output["snapshot_transport_errors"] = max(
        snapshot_transport_errors,
        int(not exact_final_completed),
        int(not settled_final_observed),
    )
    output["snapshot_requests_aborted_at_commit"] = sum(
        schedule.get("transport_status") == "aborted_at_commit" for schedule in snapshot_schedule
    )
    output["turn_cleanup"] = lifecycle
    output["trace_events"] = all_events
    return output


async def data_status(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    response = await client.get(f"{base_url}/v1/data/status")
    response.raise_for_status()
    return response.json()


def failure_output(
    *,
    row: dict[str, Any],
    repetition: int,
    path: str,
    path_order_position: int,
    run_tag: str,
    service_base_url: str,
    case_timeout_s: float,
    started_ms: float,
    error: BaseException,
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    timed_out = isinstance(error, TimeoutError)
    return {
        "schema_version": 3,
        "id": row["id"],
        "repetition": repetition,
        "path": path,
        "path_order_position": path_order_position,
        "query": row["query"],
        "query_time": row["query_time"],
        "domain": row.get("domain"),
        "question_type": row.get("question_type"),
        "dynamism": row.get("dynamism"),
        "stabilization_class": row.get("stabilization_class", "unreviewed"),
        "stabilization_confidence": row.get("stabilization_confidence", "unreviewed"),
        "stabilization_reason": row.get("stabilization_reason", ""),
        "stabilization_review_status": row.get("stabilization_review_status", "unreviewed"),
        "word_count": len(row["query"].split()),
        "service_base_url": service_base_url,
        "session_scope": session_scope(run_tag, repetition, str(row["id"]), path),
        "case_deadline_s": case_timeout_s,
        "deadline_exceeded": timed_out,
        "error_type": "case_deadline_exceeded" if timed_out else type(error).__name__,
        "error": (
            f"case deadline exceeded after {case_timeout_s:.3f}s"
            if timed_out
            else f"{type(error).__name__}: {error}"
        ),
        "turn_cleanup": lifecycle,
        "simulation_wall_ms": time.perf_counter() * 1000 - started_ms,
    }


async def run_case(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    row: dict[str, Any],
    repetition: int,
    words_per_minute: float,
    post_typing_dwell_ms: float,
    path: str,
    path_order_position: int,
    run_tag: str,
    max_typing_drift_ms: float,
    case_timeout_s: float,
) -> dict[str, Any]:
    started_ms = time.perf_counter() * 1000
    lifecycle: dict[str, Any] = {"turn_cleanup_status": "pending"}
    try:
        return await asyncio.wait_for(
            replay_path(
                client,
                base_url,
                row,
                repetition,
                words_per_minute,
                post_typing_dwell_ms,
                path,
                path_order_position,
                run_tag,
                max_typing_drift_ms,
                lifecycle,
            ),
            timeout=case_timeout_s,
        )
    except Exception as exc:
        return failure_output(
            row=row,
            repetition=repetition,
            path=path,
            path_order_position=path_order_position,
            run_tag=run_tag,
            service_base_url=base_url,
            case_timeout_s=case_timeout_s,
            started_ms=started_ms,
            error=exc,
            lifecycle=lifecycle,
        )


def reportability_gates(
    *,
    smoke: bool,
    reportable_protocol: bool,
    warmup_complete: bool,
    observed_rows: int,
    expected_rows: int,
    completed_rows: int,
    failure_count: int,
    timeout_count: int,
    drift_observations: int,
    drift_violations: int,
    max_typing_drift_ms: float,
    max_observed_abs_drift_ms: float,
    snapshot_transport_errors: int,
    turn_cleanup_failures: int,
) -> dict[str, Any]:
    timing_complete = drift_observations == completed_rows and drift_violations == 0
    snapshot_complete = snapshot_transport_errors == 0
    deadline_complete = timeout_count == 0
    cleanup_complete = turn_cleanup_failures == 0
    reportable = (
        not smoke
        and reportable_protocol
        and warmup_complete
        and observed_rows == expected_rows
        and failure_count == 0
        and timing_complete
        and snapshot_complete
        and deadline_complete
        and cleanup_complete
    )
    return {
        "reportable": reportable,
        "timing_drift_gate": {
            "status": "complete" if timing_complete else "missing_or_incomplete",
            "observations": drift_observations,
            "violations": drift_violations,
            "max_allowed_abs_drift_ms": max_typing_drift_ms,
            "max_observed_abs_drift_ms": max_observed_abs_drift_ms,
        },
        "snapshot_transport_gate": {
            "status": "complete" if snapshot_complete else "missing_or_incomplete",
            "errors": snapshot_transport_errors,
            "async_sends_do_not_delay_commit": True,
        },
        "case_deadline_gate": {
            "status": "complete" if deadline_complete else "missing_or_incomplete",
            "deadline_failures": timeout_count,
        },
        "turn_cleanup_gate": {
            "status": "complete" if cleanup_complete else "missing_or_incomplete",
            "cleanup_failures": turn_cleanup_failures,
            "failure_turns_require_bounded_delete": True,
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the frozen typed A/B benchmark")
    parser.add_argument("--naive-base-url", default="http://localhost:8001")
    parser.add_argument("--stream-base-url", default="http://localhost:8002")
    parser.add_argument(
        "--require-distinct-services",
        action="store_true",
        help=(
            "also require separate app processes for smoke runs; reportable runs "
            "always require them"
        ),
    )
    parser.add_argument(
        "--warmup-repetitions",
        type=int,
        default=0,
        help="unreported full-dataset warm-up repetitions",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--query-limit",
        type=int,
        default=10,
        help="ordered frozen-query prefix used by the reportable comparison",
    )
    parser.add_argument("--wpm", type=float, default=70.0)
    parser.add_argument(
        "--post-typing-dwell-ms",
        type=float,
        default=5000.0,
        help="fixed pause after the final character and before Send",
    )
    parser.add_argument(
        "--max-typing-drift-ms",
        type=float,
        default=100.0,
        help="absolute actual-versus-planned Send offset tolerance",
    )
    parser.add_argument(
        "--case-timeout-s",
        type=float,
        default=45.0,
        help="wall-clock deadline covering typing, commit, and the answer SSE",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_INFERENCE_DIR / "test_queries.jsonl",
    )
    parser.add_argument(
        "--smoke", action="store_true", help="allow unapproved data; not reportable"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "comparison" / "benchmark" / "results" / "predictions.jsonl",
    )
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if args.warmup_repetitions < 0:
        parser.error("--warmup-repetitions must be non-negative")
    if args.query_limit < 1:
        parser.error("--query-limit must be at least 1")
    if args.wpm <= 0:
        parser.error("--wpm must be greater than zero")
    if args.post_typing_dwell_ms < 0:
        parser.error("--post-typing-dwell-ms must be non-negative")
    if args.max_typing_drift_ms < 0:
        parser.error("--max-typing-drift-ms must be non-negative")
    if args.case_timeout_s <= 0:
        parser.error("--case-timeout-s must be greater than zero")

    naive_url = args.naive_base_url.rstrip("/")
    stream_url = args.stream_base_url.rstrip("/")
    must_isolate_services = not args.smoke or args.require_distinct_services
    if must_isolate_services and naive_url == stream_url:
        parser.error("Naive and Stream need different service URLs for this run")

    source_rows = read_jsonl(args.queries)
    rows = source_rows
    if args.smoke:
        if args.queries.name != "dev_queries.jsonl":
            parser.error("--smoke only accepts dev_queries.jsonl; unseen test queries are refused")
        dataset_manifest_path = args.queries.parent / "checksums.sha256"
        if not dataset_manifest_path.is_file():
            parser.error("--smoke queries require their adjacent dataset checksum manifest")
        dataset_checksums = read_checksum_manifest(dataset_manifest_path)
        expected_dev_queries = dataset_checksums.get("dev_queries.jsonl")
        if expected_dev_queries != sha256_file(args.queries):
            parser.error("--smoke dev queries do not match their dataset checksum manifest")
        dataset_manifest_sha256 = sha256_file(dataset_manifest_path)
        if args.query_limit > len(source_rows):
            parser.error(
                f"--query-limit {args.query_limit} exceeds the {len(source_rows)} dev queries"
            )
        rows = rows[: args.query_limit]
        args.repetitions = 1
        args.warmup_repetitions = 0
        evaluation_manifest_sha256 = ""
        freeze_id = ""
    else:
        inference_bundle_path = args.queries.parent / "inference_bundle.json"
        if not inference_bundle_path.is_file():
            raise SystemExit(
                f"redacted inference bundle metadata is missing: {inference_bundle_path}"
            )
        inference_bundle = json.loads(inference_bundle_path.read_text(encoding="utf-8"))
        if inference_bundle.get("bundle_role") != "inference_corpus":
            raise SystemExit("benchmark queries must come from a redacted inference_corpus bundle")
        if inference_bundle.get("approval_status") != "approved_frozen":
            raise SystemExit("inference bundle is not approved_frozen")
        evaluation_manifest_sha256 = str(inference_bundle.get("evaluation_manifest_sha256") or "")
        freeze_id = str(inference_bundle.get("freeze_id") or "")
        if freeze_id != opaque_freeze_id(evaluation_manifest_sha256):
            raise SystemExit("inference bundle opaque freeze_id is invalid")
        if inference_bundle.get("test_queries_sha256") != sha256_file(args.queries):
            raise SystemExit("query file does not match inference bundle metadata")
        documents_name = str(inference_bundle.get("documents_filename") or "documents.jsonl")
        if documents_name not in {"documents.jsonl", "documents.jsonl.bz2"}:
            raise SystemExit("inference bundle documents_filename is invalid")
        if any("gold" in path.name.casefold() for path in args.queries.parent.iterdir()):
            raise SystemExit("inference bundle directory must not contain scorer-only gold")
        dataset_manifest_path = args.queries.parent / "checksums.sha256"
        if not dataset_manifest_path.is_file():
            raise SystemExit(f"dataset checksum manifest is missing: {dataset_manifest_path}")
        dataset_manifest_sha256 = sha256_file(dataset_manifest_path)
        dataset_checksums = read_checksum_manifest(dataset_manifest_path)
        if any("gold" in name.casefold() for name in dataset_checksums):
            raise SystemExit("inference checksum manifest must not name scorer-only gold")
        expected_inference_entries = {
            "dataset_summary.json",
            documents_name,
            "test_queries.jsonl",
            "inference_bundle.json",
        }
        if set(dataset_checksums) != expected_inference_entries:
            raise SystemExit("inference checksum manifest has unexpected or missing entries")
        for dataset_file in (
            args.queries.parent / "dataset_summary.json",
            args.queries.parent / documents_name,
            args.queries,
            inference_bundle_path,
        ):
            expected = dataset_checksums.get(dataset_file.name)
            if (
                expected is None
                or not dataset_file.is_file()
                or sha256_file(dataset_file) != expected
            ):
                raise SystemExit(
                    f"{dataset_file.name} is not bound to the dataset checksum manifest"
                )
        if args.query_limit != 10 or len(source_rows) < args.query_limit:
            raise SystemExit("reportable benchmark requires the ordered first 10 frozen queries")
        rows = source_rows[: args.query_limit]
        query_ids = [str(row.get("id") or "") for row in rows]
        if len(set(query_ids)) != 10 or any(not value for value in query_ids):
            raise SystemExit("reportable benchmark requires 10 uniquely identified frozen queries")
    run_tag = uuid.uuid4().hex[:10]
    timeout = httpx.Timeout(30.0, read=None)
    async with (
        httpx.AsyncClient(timeout=timeout) as naive_client,
        httpx.AsyncClient(timeout=timeout) as stream_client,
    ):
        statuses = {
            "naive": await data_status(naive_client, naive_url),
            "stream": await data_status(stream_client, stream_url),
        }
        for path, status in statuses.items():
            identity_issues = service_identity_issues(status, path, root=ROOT)
            if identity_issues:
                raise SystemExit(f"{path} service identity is invalid: {identity_issues}")
            if status["indexed_chunks"] <= 0:
                raise SystemExit(f"{path} Qdrant index is empty; sync data before benchmarking")
            if args.smoke and status.get("serving_dataset_checksum") != dataset_manifest_sha256:
                raise SystemExit(
                    f"{path} serves a different corpus than the selected dev smoke queries"
                )
            if args.smoke and status.get("dataset_checksums_valid") is not True:
                raise SystemExit(f"{path} development dataset checksum verification failed")
            if args.smoke and status.get("index_matches_current_corpus") is not True:
                raise SystemExit(f"{path} development index is stale for its corpus/config")
            if args.smoke and int(status["indexed_chunks"]) != int(
                status.get("indexed_desired_chunks") or -1
            ):
                raise SystemExit(f"{path} development index point count is incomplete")
            if args.smoke:
                expected_configuration = {
                    "model": "gpt-5.6-sol",
                    "embedding_model": "text-embedding-3-large",
                    "reasoning_effort": "medium",
                    "summary_reasoning_effort": "low",
                    "service_tier": "default",
                }
                mismatches = [
                    key for key, value in expected_configuration.items() if status.get(key) != value
                ]
                if mismatches:
                    raise SystemExit(
                        f"{path} development service has the wrong locked configuration: "
                        f"{mismatches}"
                    )
                if path == "stream" and (
                    status.get("trigger_reasoning_effort") != "low"
                    or int(status.get("settled_draft_delay_ms") or -1)
                    != SETTLED_DRAFT_DELAY_MS
                ):
                    raise SystemExit(
                        "stream development service has the wrong typed-input configuration"
                    )
            if status["approval_status"] != "approved_frozen" and not args.smoke:
                raise SystemExit(f"{path} dataset is not approved_frozen; final benchmark refused")
            if not args.smoke and status.get("dataset_checksums_valid") is not True:
                raise SystemExit(f"{path} dataset checksum verification failed")
            if not args.smoke and status.get("index_matches_current_corpus") is not True:
                raise SystemExit(f"{path} index is stale for the current corpus/config")
        comparable_candidates = COMMON_IDENTITY_FIELDS
        if not args.smoke:
            missing_status_fields = {
                path: [key for key in comparable_candidates if status.get(key) is None]
                for path, status in statuses.items()
            }
            missing_status_fields = {
                path: fields for path, fields in missing_status_fields.items() if fields
            }
            if missing_status_fields:
                raise SystemExit(
                    "reportable benchmark requires complete backend identities: "
                    f"{missing_status_fields}"
                )
            for path, status in statuses.items():
                if status["dataset_checksums_valid"] is not True:
                    raise SystemExit(f"{path} backend dataset checksum validation failed")
                if status["index_matches_current_corpus"] is not True:
                    raise SystemExit(f"{path} index does not match its current corpus/config")
                if int(status["indexed_chunks"]) != int(status["indexed_desired_chunks"]):
                    raise SystemExit(f"{path} index point count does not match desired chunks")
                if status["index_source_sha256"] != status["current_index_source_sha256"]:
                    raise SystemExit(f"{path} index source fingerprint is stale")
                if status["dataset_checksum"] != evaluation_manifest_sha256:
                    raise SystemExit(
                        f"{path} backend evaluation freeze ID does not match the query bundle"
                    )
                if status["serving_dataset_checksum"] != dataset_manifest_sha256:
                    raise SystemExit(f"{path} backend serving bundle checksum does not match")
                if status["freeze_id"] != freeze_id:
                    raise SystemExit(f"{path} backend opaque freeze_id does not match")
        comparable_fields = tuple(
            key
            for key in comparable_candidates
            if statuses["naive"].get(key) is not None and statuses["stream"].get(key) is not None
        )
        if any(
            statuses["naive"].get(key) != statuses["stream"].get(key) for key in comparable_fields
        ):
            raise SystemExit(
                "Naive and Stream services do not expose the same dataset/index status"
            )
        instance_ids = {
            path: str(status.get("instance_id") or "") for path, status in statuses.items()
        }
        distinct_instances = bool(
            instance_ids["naive"]
            and instance_ids["stream"]
            and instance_ids["naive"] != instance_ids["stream"]
        )
        if must_isolate_services and not distinct_instances:
            raise SystemExit(
                "benchmark requires two non-empty, different backend instance_id values"
            )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = args.output.with_suffix(".manifest.json")
        archived_queries_path = args.queries
        archived_serving_manifest_path = dataset_manifest_path
        if not args.smoke:
            # Keep the final run independently scoreable after the ignored serving
            # bundle is removed. These are gold-free provenance files: the exact
            # test queries and the serving checksum manifest, never test gold.
            archived_queries_path = args.output.with_suffix(".queries.jsonl")
            archived_serving_manifest_path = args.output.with_suffix(".serving-checksums.sha256")
            shutil.copyfile(args.queries, archived_queries_path)
            if dataset_manifest_path is None:
                raise RuntimeError("reportable run is missing its serving checksum manifest")
            shutil.copyfile(dataset_manifest_path, archived_serving_manifest_path)
        reportable_protocol = (
            args.warmup_repetitions == 0
            and args.repetitions == 1
            and args.query_limit == 10
            and len(rows) == 10
            and args.wpm == 70.0
            and args.post_typing_dwell_ms == 5000.0
            and int(statuses["stream"]["settled_draft_delay_ms"]) == SETTLED_DRAFT_DELAY_MS
            and args.max_typing_drift_ms == 100.0
            and args.case_timeout_s == 45.0
        )
        manifest: dict[str, Any] = {
            "schema_version": 4,
            "run_tag": run_tag,
            "created_unix_s": time.time(),
            "queries": manifest_relative_path(archived_queries_path, manifest_path),
            "queries_sha256": sha256_file(args.queries),
            "query_ids": [str(row["id"]) for row in rows],
            "query_count": len(rows),
            "query_selection": {
                "method": "ordered_prefix",
                "source_query_count": len(source_rows),
                "selected_query_count": len(rows),
            },
            "freeze_id": freeze_id,
            "evaluation_manifest_sha256": evaluation_manifest_sha256,
            "serving_checksum_manifest": (
                manifest_relative_path(archived_serving_manifest_path, manifest_path)
                if archived_serving_manifest_path
                else None
            ),
            "serving_checksum_manifest_sha256": dataset_manifest_sha256,
            "benchmark_harness_sha256": {
                "run_benchmark.py": sha256_file(Path(__file__).resolve()),
                "typed_trace.py": sha256_file(ROOT / "comparison" / "benchmark" / "typed_trace.py"),
            },
            "predictions": manifest_relative_path(args.output, manifest_path),
            "predictions_sha256": None,
            "prediction_rows_expected": len(rows) * args.repetitions * 2,
            "prediction_rows_observed": 0,
            "finalized": False,
            "run_status": "initialized",
            "reportable": False,
            "warmup_repetitions": args.warmup_repetitions,
            "warmup_outputs_expected": len(rows) * args.warmup_repetitions * 2,
            "warmup_outputs_completed": 0,
            "warmup_failures": 0,
            "repetitions": args.repetitions,
            "words_per_minute": args.wpm,
            "post_typing_dwell_ms": args.post_typing_dwell_ms,
            "settled_draft_delay_ms": SETTLED_DRAFT_DELAY_MS,
            "case_deadline_s": args.case_timeout_s,
            "max_typing_drift_ms": args.max_typing_drift_ms,
            "smoke_non_reportable": args.smoke,
            "path_urls": {"naive": naive_url, "stream": stream_url},
            "distinct_service_urls": naive_url != stream_url,
            "backend_instance_ids": instance_ids,
            "distinct_backend_instances": distinct_instances,
            "compared_status_fields": list(comparable_fields),
            "text_input_contract": {
                "snapshots": (
                    "400 ms changed-only cumulative dirty-text deliveries, including partial "
                    "words; unchanged ticks emit no request"
                ),
                "sampling_boundary": (
                    "the sampler ticks strictly before deterministic Send time through the "
                    "fixed post-typing dwell, but only changed text is delivered"
                ),
                "post_typing_dwell_ms": args.post_typing_dwell_ms,
                "commit": (
                    "Send carries full text at a higher revision; dirty snapshots never commit "
                    "or produce an answer"
                ),
                "ui_snapshot_sampling_ms": 400,
                "server_trigger_interval_ms": 500,
                "server_settled_draft_delay_ms": SETTLED_DRAFT_DELAY_MS,
                "trigger_min_words": 5,
                "snapshot_transport": (
                    "asynchronous requests never delay the planned Send boundary; the exact "
                    "full-draft dwell snapshot must complete, obsolete prefix requests may be "
                    "aborted at Send, and other transport failures make the run non-reportable"
                ),
                "excluded": ["ASR latency", "endpoint detection", "trailing silence"],
            },
            "preregistered_protocol_gate": {
                "status": "complete" if reportable_protocol else "missing_or_incomplete",
                "required_query_count": 10,
                "required_total_path_runs": 20,
                "required_warmup_repetitions": 0,
                "required_measured_repetitions": 1,
                "required_words_per_minute": 70.0,
                "required_post_typing_dwell_ms": 5000.0,
                "required_settled_draft_delay_ms": SETTLED_DRAFT_DELAY_MS,
                "required_max_typing_drift_ms": 100.0,
                "required_case_deadline_s": 45.0,
            },
            "warmup_gate": {"status": "pending"},
            "timing_drift_gate": {"status": "pending"},
            "snapshot_transport_gate": {"status": "pending"},
            "case_deadline_gate": {"status": "pending"},
            "turn_cleanup_gate": {"status": "pending"},
            "cache_isolation": (
                "unique session scope per repetition/query/path; separate service processes"
                if distinct_instances
                else "unique session scope per repetition/query/path within one service"
            ),
            "scorer_isolation": {
                "runner_gold_access": "forbidden",
                "binding": "query SHA-256 plus opaque evaluation freeze_id",
            },
            "data_status": statuses,
        }

        def write_manifest() -> None:
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        write_manifest()
        clients = {"naive": naive_client, "stream": stream_client}
        urls = {"naive": naive_url, "stream": stream_url}
        args.output.write_text("", encoding="utf-8")
        observed_rows = 0
        completed_rows = 0
        failure_count = 0
        timeout_count = 0
        drift_observations = 0
        drift_violations = 0
        max_observed_abs_drift_ms = 0.0
        snapshot_transport_errors = 0
        turn_cleanup_failures = 0
        try:
            manifest["run_status"] = "warming_up"
            write_manifest()
            warmup_failures = 0
            warmup_cleanup_failures = 0
            warmup_outputs_completed = 0
            for warmup_index in range(args.warmup_repetitions):
                warmup_repetition = -warmup_index
                for number, row in enumerate(rows, 1):
                    path_order = (
                        ("naive", "stream")
                        if (warmup_index + number) % 2 == 0
                        else ("stream", "naive")
                    )
                    for position, path in enumerate(path_order, 1):
                        warmup_output = await run_case(
                            client=clients[path],
                            base_url=urls[path],
                            row=row,
                            repetition=warmup_repetition,
                            words_per_minute=args.wpm,
                            post_typing_dwell_ms=args.post_typing_dwell_ms,
                            path=path,
                            path_order_position=position,
                            run_tag=run_tag,
                            max_typing_drift_ms=args.max_typing_drift_ms,
                            case_timeout_s=args.case_timeout_s,
                        )
                        warmup_outputs_completed += 1
                        warmup_failures += "error" in warmup_output
                        if "error" in warmup_output:
                            warmup_cleanup_failures += (
                                warmup_output.get("turn_cleanup", {}).get("turn_cleanup_status")
                                != "completed"
                            )
                    print(
                        f"warm-up {warmup_index + 1}/{args.warmup_repetitions} "
                        f"query {number}/{len(rows)} {row['id']}",
                        file=sys.stderr,
                    )
            manifest.update(
                {
                    "warmup_outputs_completed": warmup_outputs_completed,
                    "warmup_failures": warmup_failures,
                    "warmup_gate": {
                        "status": (
                            "complete"
                            if warmup_outputs_completed == manifest["warmup_outputs_expected"]
                            and warmup_failures == 0
                            and warmup_cleanup_failures == 0
                            else "missing_or_incomplete"
                        ),
                        "outputs_expected": manifest["warmup_outputs_expected"],
                        "outputs_completed": warmup_outputs_completed,
                        "failures": warmup_failures,
                        "cleanup_failures": warmup_cleanup_failures,
                        "retained_in_predictions": False,
                    },
                }
            )
            write_manifest()
            if warmup_failures:
                raise RuntimeError("warm-up failed; measured repetitions were not started")

            manifest["run_status"] = "measuring"
            write_manifest()
            with args.output.open("w", encoding="utf-8") as handle:
                for repetition in range(1, args.repetitions + 1):
                    for number, row in enumerate(rows, 1):
                        path_order = (
                            ("naive", "stream")
                            if (repetition + number) % 2 == 0
                            else ("stream", "naive")
                        )
                        for position, path in enumerate(path_order, 1):
                            output = await run_case(
                                client=clients[path],
                                base_url=urls[path],
                                row=row,
                                repetition=repetition,
                                words_per_minute=args.wpm,
                                post_typing_dwell_ms=args.post_typing_dwell_ms,
                                path=path,
                                path_order_position=position,
                                run_tag=run_tag,
                                max_typing_drift_ms=args.max_typing_drift_ms,
                                case_timeout_s=args.case_timeout_s,
                            )
                            observed_rows += 1
                            if "error" in output:
                                failure_count += 1
                                timeout_count += output.get("deadline_exceeded") is True
                                turn_cleanup_failures += (
                                    output.get("turn_cleanup", {}).get("turn_cleanup_status")
                                    != "completed"
                                )
                            else:
                                completed_rows += 1
                                turn_cleanup_failures += (
                                    output.get("turn_cleanup", {}).get("turn_cleanup_status")
                                    != "not_required"
                                )
                                typing = output.get("typing", {})
                                drift = abs(float(typing.get("commit_drift_ms") or 0.0))
                                drift_observations += 1
                                drift_violations += drift > args.max_typing_drift_ms
                                max_observed_abs_drift_ms = max(max_observed_abs_drift_ms, drift)
                                snapshot_transport_errors += int(
                                    output.get("snapshot_transport_errors") or 0
                                )
                            handle.write(
                                json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n"
                            )
                        handle.flush()
                        print(
                            f"repetition {repetition}/{args.repetitions} "
                            f"query {number}/{len(rows)} {row['id']}",
                            file=sys.stderr,
                        )

            expected_rows = int(manifest["prediction_rows_expected"])
            gates = reportability_gates(
                smoke=args.smoke,
                reportable_protocol=reportable_protocol,
                warmup_complete=manifest["warmup_gate"]["status"] == "complete",
                observed_rows=observed_rows,
                expected_rows=expected_rows,
                completed_rows=completed_rows,
                failure_count=failure_count,
                timeout_count=timeout_count,
                drift_observations=drift_observations,
                drift_violations=drift_violations,
                max_typing_drift_ms=args.max_typing_drift_ms,
                max_observed_abs_drift_ms=max_observed_abs_drift_ms,
                snapshot_transport_errors=snapshot_transport_errors,
                turn_cleanup_failures=turn_cleanup_failures,
            )
            gates["case_deadline_gate"]["deadline_s"] = args.case_timeout_s
            reportable = bool(gates["reportable"])
            manifest.update(
                {
                    "completed_unix_s": time.time(),
                    "predictions_sha256": sha256_file(args.output),
                    "prediction_rows_observed": observed_rows,
                    "completed_outputs": completed_rows,
                    "failures": failure_count,
                    "deadline_failures": timeout_count,
                    **gates,
                    "run_status": (
                        "completed_reportable" if reportable else "completed_non_reportable"
                    ),
                    "reportable": reportable,
                    "finalized": True,
                }
            )
            write_manifest()
        except BaseException as exc:
            if manifest.get("finalized") is not True:
                manifest.update(
                    {
                        "completed_unix_s": time.time(),
                        "predictions_sha256": (
                            sha256_file(args.output) if args.output.is_file() else None
                        ),
                        "prediction_rows_observed": observed_rows,
                        "completed_outputs": completed_rows,
                        "failures": failure_count,
                        "deadline_failures": timeout_count,
                        "run_status": "aborted",
                        "terminal_error_type": type(exc).__name__,
                        "reportable": False,
                        "finalized": True,
                    }
                )
                write_manifest()
            raise


if __name__ == "__main__":
    asyncio.run(main())
