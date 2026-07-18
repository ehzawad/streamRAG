#!/usr/bin/env python3
"""Score the checksummed development comparison without touching unseen test gold."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from bench.score import (
        annotate,
        backend_source_sha256,
        config_sha256,
        opaque_freeze_id,
        paired_summary,
        path_summary,
        read_checksum_manifest,
        read_jsonl,
        resolve_manifest_path,
        settings,
        sha256_file,
    )
except ModuleNotFoundError:  # Direct execution places bench/ on sys.path.
    from score import (
        annotate,
        backend_source_sha256,
        config_sha256,
        opaque_freeze_id,
        paired_summary,
        path_summary,
        read_checksum_manifest,
        read_jsonl,
        resolve_manifest_path,
        settings,
        sha256_file,
    )

ROOT = Path(__file__).resolve().parents[1]
DEV_REQUIRED_IDENTITY_FIELDS = {
    "approval_status",
    "backend_source_sha256",
    "config_hash",
    "current_index_source_sha256",
    "dataset_checksum",
    "dataset_checksums_valid",
    "dataset_sha256",
    "documents_sha256",
    "embedding_model",
    "freeze_id",
    "index_checksum",
    "index_matches_current_corpus",
    "index_metadata_ready",
    "index_pipeline_version",
    "index_source_sha256",
    "index_version",
    "indexed_chunks",
    "indexed_desired_chunks",
    "model",
    "reasoning_effort",
    "settled_draft_delay_ms",
    "service_tier",
    "serving_dataset_checksum",
    "summary_reasoning_effort",
    "trigger_reasoning_effort",
}
DEV_CONFIGURATION = {
    "model": "gpt-5.6-sol",
    "embedding_model": "text-embedding-3-large",
    "reasoning_effort": "medium",
    "trigger_reasoning_effort": "low",
    "summary_reasoning_effort": "low",
    "settled_draft_delay_ms": 500,
    "service_tier": "default",
}
DEV_PROTOCOL_GATE = {
    "status": "missing_or_incomplete",
    "required_query_count": 10,
    "required_total_path_runs": 20,
    "required_warmup_repetitions": 0,
    "required_measured_repetitions": 1,
    "required_words_per_minute": 70.0,
    "required_post_typing_dwell_ms": 5000.0,
    "required_settled_draft_delay_ms": 500,
    "required_max_typing_drift_ms": 100.0,
    "required_case_deadline_s": 45.0,
}


def _integer(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _number(value: Any, default: float = -1.0) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return default


def stabilization_summaries(
    annotated: list[dict[str, Any]],
    gold_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    labels = sorted({str(row.get("stabilization_class") or "unreviewed") for row in annotated})
    for label in labels:
        rows = [row for row in annotated if row.get("stabilization_class") == label]
        by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_path[str(row["path"])].append(row)
        path_metrics = {
            path: path_summary(items, gold_by_id) for path, items in sorted(by_path.items())
        }
        paired = paired_summary(rows)
        summaries[label] = {
            "query_count": len({str(row["id"]) for row in rows}),
            "paths": {
                path: {
                    "expected_answer_accuracy": metrics["expected_answer_accuracy"],
                    "supported_expected_answer_rate": metrics["supported_expected_answer_rate"],
                    "median_ttft_ms": metrics["median_ttft_ms"],
                    "median_total_ms": metrics["median_total_ms"],
                    "mean_cost_usd": metrics["mean_cost_usd"],
                }
                for path, metrics in path_metrics.items()
            },
            "paired": {
                "stream_ttft_win_rate": paired["stream_ttft_win_rate"],
                "median_stream_minus_naive_ttft_ms": paired["median_stream_minus_naive_ttft_ms"],
                "median_stream_minus_naive_ttft_percent": paired[
                    "median_stream_minus_naive_ttft_percent"
                ],
                "median_stream_minus_naive_total_ms": paired["median_stream_minus_naive_total_ms"],
                "mean_stream_minus_naive_accuracy": paired["mean_stream_minus_naive_accuracy"],
            },
        }
    return summaries


def validate_dev_run(
    rows: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    predictions: Path,
) -> dict[str, Any]:
    manifest_path = predictions.with_suffix(".manifest.json")
    issues: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "issues": [f"dev manifest unavailable: {exc}"]}

    if manifest.get("schema_version") != 4:
        issues.append("development manifest schema_version must be 4")
    if manifest.get("smoke_non_reportable") is not True:
        issues.append("run is not explicitly marked development-only/non-reportable")
    if (
        manifest.get("finalized") is not True
        or manifest.get("reportable") is not False
        or manifest.get("run_status") != "completed_non_reportable"
    ):
        issues.append("development run did not complete cleanly")

    repetitions = _integer(manifest.get("repetitions"))
    warmup_repetitions = _integer(manifest.get("warmup_repetitions"))
    if repetitions != 1 or warmup_repetitions != 0:
        issues.append("development comparison requires no warm-up and one measured repetition")
    if (
        _number(manifest.get("words_per_minute")) != 70.0
        or _number(manifest.get("post_typing_dwell_ms")) != 5000.0
        or _number(manifest.get("settled_draft_delay_ms")) != 500.0
        or _number(manifest.get("max_typing_drift_ms")) != 100.0
        or _number(manifest.get("case_deadline_s")) != 45.0
    ):
        issues.append("development typed-input timing contract is invalid")
    if manifest.get("preregistered_protocol_gate") != DEV_PROTOCOL_GATE:
        issues.append("development preregistered protocol gate is invalid")

    try:
        manifest_predictions = resolve_manifest_path(manifest_path, manifest.get("predictions"))
    except ValueError as exc:
        manifest_predictions = Path()
        issues.append(str(exc))
    if not manifest_predictions.is_file():
        issues.append("manifest prediction file is missing")
    elif manifest_predictions != predictions.resolve():
        issues.append("manifest prediction path does not match the scored file")
    expected_predictions_sha256 = str(manifest.get("predictions_sha256") or "")
    if (
        not predictions.is_file()
        or not expected_predictions_sha256
        or sha256_file(predictions) != expected_predictions_sha256
    ):
        issues.append("prediction checksum does not match the finalized manifest")

    expected_harness_hashes = {
        "run_benchmark.py": sha256_file(ROOT / "bench" / "run_benchmark.py"),
        "typed_trace.py": sha256_file(ROOT / "bench" / "typed_trace.py"),
    }
    if manifest.get("benchmark_harness_sha256") != expected_harness_hashes:
        issues.append("benchmark harness source does not match the content-addressed run")

    source_path: Path | None = None
    source_rows: list[dict[str, Any]] = []
    source_paths = {str(row.get("_source_path") or "") for row in dev_rows}
    if len(source_paths) != 1 or not next(iter(source_paths), ""):
        issues.append("development rows do not identify one source file")
    else:
        source_path = Path(next(iter(source_paths))).resolve()
        try:
            source_rows = read_jsonl(source_path)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"development source file is unavailable or invalid: {exc}")
    try:
        manifest_queries = resolve_manifest_path(manifest_path, manifest.get("queries"))
    except ValueError as exc:
        manifest_queries = Path()
        issues.append(str(exc))
    query_rows: list[dict[str, Any]] = []
    if not manifest_queries.is_file():
        issues.append("manifest query file is missing")
    else:
        expected_queries_sha256 = str(manifest.get("queries_sha256") or "")
        if not expected_queries_sha256 or sha256_file(manifest_queries) != expected_queries_sha256:
            issues.append("manifest query checksum does not match its query file")
        try:
            query_rows = read_jsonl(manifest_queries)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"manifest query file is invalid: {exc}")
    if source_path is not None and manifest_queries != source_path:
        issues.append("manifest query path does not match the selected development file")
    if source_path is not None and source_path.is_file():
        source_sha256 = sha256_file(source_path)
        if str(manifest.get("queries_sha256") or "") != source_sha256:
            issues.append("predictions were not generated from the selected development file")

    source_ids = [str(row.get("id") or "") for row in source_rows]
    query_ids = [str(value) for value in (manifest.get("query_ids") or [])]
    query_count = _integer(manifest.get("query_count"))
    if not source_ids or any(not query_id for query_id in source_ids):
        issues.append("development source query IDs are missing")
    if len(source_ids) != len(set(source_ids)):
        issues.append("development source contains duplicate query IDs")
    if len(query_ids) != len(set(query_ids)):
        issues.append("manifest query_ids contain duplicates")
    if query_count != len(query_ids) or query_ids != source_ids:
        issues.append("manifest query IDs do not exactly match the development file")
    expected_selection = {
        "method": "ordered_prefix",
        "source_query_count": len(source_rows),
        "selected_query_count": len(source_rows),
    }
    if manifest.get("query_selection") != expected_selection:
        issues.append("development query selection contract is invalid")
    if query_rows != source_rows:
        issues.append("manifest query payloads do not exactly match the development file")

    serving_manifest_path = Path()
    serving_checksums: dict[str, str] = {}
    try:
        serving_manifest_path = resolve_manifest_path(
            manifest_path, manifest.get("serving_checksum_manifest")
        )
        serving_checksums = read_checksum_manifest(serving_manifest_path)
    except (OSError, ValueError) as exc:
        issues.append(f"development checksum manifest is unavailable or invalid: {exc}")
    serving_manifest_sha256 = (
        sha256_file(serving_manifest_path) if serving_manifest_path.is_file() else ""
    )
    if serving_manifest_sha256 != str(manifest.get("serving_checksum_manifest_sha256") or ""):
        issues.append("development checksum manifest hash does not match the run")
    if source_path is not None and serving_manifest_path != source_path.parent / "checksums.sha256":
        issues.append("run does not use the development file's adjacent checksum manifest")
    if source_path is not None and serving_checksums.get(source_path.name) != str(
        manifest.get("queries_sha256") or ""
    ):
        issues.append("development checksum manifest does not bind the query file")

    statuses = manifest.get("data_status")
    statuses = statuses if isinstance(statuses, dict) else {}
    compared_status_fields = manifest.get("compared_status_fields")
    compared_fields = (
        set(compared_status_fields) if isinstance(compared_status_fields, list) else set()
    )
    if not DEV_REQUIRED_IDENTITY_FIELDS <= compared_fields:
        issues.append("manifest does not compare every development service identity")
    expected_backend_source = backend_source_sha256(ROOT)
    expected_config = config_sha256(settings)
    for path in ("naive", "stream"):
        status = statuses.get(path)
        if not isinstance(status, dict):
            issues.append(f"{path} development status is missing")
            continue
        if any(status.get(field) is None for field in DEV_REQUIRED_IDENTITY_FIELDS):
            issues.append(f"{path} status is missing required service identities")
        configuration_mismatches = [
            field for field, value in DEV_CONFIGURATION.items() if status.get(field) != value
        ]
        if configuration_mismatches:
            issues.append(
                f"{path} status has the wrong development configuration: {configuration_mismatches}"
            )
        if status.get("backend_source_sha256") != expected_backend_source:
            issues.append(f"{path} backend source does not match the current application tree")
        if status.get("config_hash") != expected_config:
            issues.append(f"{path} runtime configuration does not match the current scorer")
        if status.get("approval_status") != "candidate_pending_human_review":
            issues.append(f"{path} status is not the development candidate")
        if (
            status.get("dataset_checksums_valid") is not True
            or status.get("index_metadata_ready") is not True
            or status.get("index_matches_current_corpus") is not True
            or _integer(status.get("indexed_chunks")) <= 0
            or _integer(status.get("indexed_chunks"))
            != _integer(status.get("indexed_desired_chunks"))
            or status.get("index_source_sha256") != status.get("current_index_source_sha256")
        ):
            issues.append(f"{path} status does not prove a complete current index")
        if (
            not serving_manifest_sha256
            or status.get("dataset_checksum") != serving_manifest_sha256
            or status.get("serving_dataset_checksum") != serving_manifest_sha256
            or status.get("freeze_id") != opaque_freeze_id(serving_manifest_sha256)
        ):
            issues.append(f"{path} status does not match the checksummed development bundle")
        document_entries = {
            name: digest
            for name, digest in serving_checksums.items()
            if name in {"documents.jsonl", "documents.jsonl.bz2"}
        }
        if len(document_entries) != 1 or status.get("documents_sha256") != next(
            iter(document_entries.values()), None
        ):
            issues.append(f"{path} document identity does not match the checksum manifest")
    if all(isinstance(statuses.get(path), dict) for path in ("naive", "stream")):
        identity_mismatches = sorted(
            field
            for field in DEV_REQUIRED_IDENTITY_FIELDS | {"index_metadata_ready"}
            if statuses["naive"].get(field) != statuses["stream"].get(field)
        )
        if identity_mismatches:
            issues.append(f"development backend identity mismatch: {identity_mismatches}")

    path_urls = manifest.get("path_urls")
    path_urls = path_urls if isinstance(path_urls, dict) else {}
    backend_instance_ids = manifest.get("backend_instance_ids")
    backend_instance_ids = backend_instance_ids if isinstance(backend_instance_ids, dict) else {}
    urls_are_distinct = bool(
        str(path_urls.get("naive") or "")
        and str(path_urls.get("stream") or "")
        and str(path_urls.get("naive")).rstrip("/") != str(path_urls.get("stream")).rstrip("/")
    )
    instances_are_distinct = bool(
        str(backend_instance_ids.get("naive") or "")
        and str(backend_instance_ids.get("stream") or "")
        and backend_instance_ids.get("naive") != backend_instance_ids.get("stream")
        and all(
            isinstance(statuses.get(path), dict)
            and statuses[path].get("instance_id") == backend_instance_ids.get(path)
            for path in ("naive", "stream")
        )
    )
    if manifest.get("distinct_service_urls") is not True or not urls_are_distinct:
        issues.append("manifest does not bind distinct development service URLs")
    if manifest.get("distinct_backend_instances") is not True or not instances_are_distinct:
        issues.append("manifest does not bind two matching, distinct backend instances")

    selected_ids = source_ids
    observed = [
        (str(row.get("id")), _integer(row.get("repetition")), str(row.get("path"))) for row in rows
    ]
    counts = Counter(observed)
    expected = {(query_id, 1, path) for query_id in selected_ids for path in ("naive", "stream")}
    if set(observed) != expected or any(value != 1 for value in counts.values()):
        issues.append("predictions are not exactly one Naive and one Stream run per dev query")
    if len(rows) != len(expected):
        issues.append("development prediction row count is inconsistent")
    if _integer(manifest.get("prediction_rows_expected")) != len(expected):
        issues.append("manifest prediction_rows_expected is inconsistent")
    if _integer(manifest.get("prediction_rows_observed")) != len(rows):
        issues.append("manifest prediction_rows_observed is inconsistent")
    if _integer(manifest.get("completed_outputs")) != len(rows):
        issues.append("manifest completed output count is inconsistent")
    if _integer(manifest.get("failures")) != 0 or _integer(manifest.get("deadline_failures")) != 0:
        issues.append("manifest records development output failures")

    query_by_id = {str(row.get("id")): row for row in source_rows}
    query_mismatches = sorted(
        {
            str(row.get("id"))
            for row in rows
            if str(row.get("id")) not in query_by_id
            or row.get("query") != query_by_id[str(row.get("id"))].get("query")
            or row.get("query_time") != query_by_id[str(row.get("id"))].get("query_time")
        }
    )
    if query_mismatches:
        issues.append(f"prediction query payload mismatch: {query_mismatches[:10]}")

    run_tag = str(manifest.get("run_tag") or "")
    provenance_mismatches = []
    malformed_timing = []
    drift_violations = []
    snapshot_contract_violations = []
    snapshot_transport_errors = 0
    cleanup_failures = []
    observed_drifts: list[float] = []
    for row in rows:
        key = (str(row.get("id")), _integer(row.get("repetition")), str(row.get("path")))
        path = key[2]
        expected_scope = f"bench-{run_tag}-{key[1]}-{key[0]}-{path}"
        expected_url = str(path_urls.get(path) or "").rstrip("/")
        if (
            not run_tag
            or row.get("session_scope") != expected_scope
            or not expected_url
            or str(row.get("service_base_url") or "").rstrip("/") != expected_url
        ):
            provenance_mismatches.append(key)
        if "error" in row:
            continue
        typing = row.get("typing")
        try:
            if not isinstance(typing, dict):
                raise TypeError
            planned = float(typing["planned_commit_offset_ms"])
            actual = float(typing["actual_commit_offset_ms"])
            recorded_drift = float(typing["commit_drift_ms"])
            tolerance = float(typing["max_allowed_drift_ms"])
            typing_duration = float(typing["typing_duration_ms"])
            dwell = float(typing["post_typing_dwell_ms"])
            simulated_duration = float(typing["simulated_duration_ms"])
            words_per_minute = float(typing["words_per_minute"])
            snapshot_interval_ms = int(typing["snapshot_interval_ms"])
            settled_draft_delay_ms = int(typing["settled_draft_delay_ms"])
        except KeyError, TypeError, ValueError:
            malformed_timing.append(key)
            continue
        observed_drifts.append(abs(recorded_drift))
        if (
            not math.isclose(actual - planned, recorded_drift, abs_tol=1.0)
            or not math.isclose(tolerance, 100.0, abs_tol=1e-6)
            or typing.get("drift_within_tolerance") is not True
            or abs(recorded_drift) > 100.0
        ):
            drift_violations.append(key)
        if (
            not math.isclose(words_per_minute, 70.0, abs_tol=1e-6)
            or not math.isclose(dwell, 5000.0, abs_tol=1e-6)
            or snapshot_interval_ms != 400
            or settled_draft_delay_ms != 500
            or not math.isclose(typing_duration + dwell, planned, abs_tol=1.0)
            or not math.isclose(simulated_duration, planned, abs_tol=1.0)
        ):
            malformed_timing.append(key)
        schedules = row.get("snapshot_schedule")
        if not isinstance(schedules, list):
            snapshot_contract_violations.append(key)
            schedules = []
        snapshot_transport_errors += _integer(row.get("snapshot_transport_errors"), 0)
        invalid_transport = any(
            not isinstance(schedule, dict)
            or schedule.get("transport_status") not in {"completed", "aborted_at_commit"}
            for schedule in schedules
        )
        if invalid_transport:
            snapshot_contract_violations.append(key)
        elif path == "naive" and schedules:
            snapshot_contract_violations.append(key)
        elif path == "stream":
            exact_snapshots = [
                schedule
                for schedule in schedules
                if schedule.get("is_final") is True
                and _integer(schedule.get("character_count"))
                == len(str(row.get("query") or "").strip())
            ]
            exact_revision = (
                _integer(exact_snapshots[0].get("revision")) if len(exact_snapshots) == 1 else -1
            )
            expected_query = str(row.get("query") or "").strip()
            settled_events = [
                event
                for event in row.get("trace_events", [])
                if isinstance(event, dict)
                and event.get("type") == "draft.settled"
                and _integer(event.get("revision")) == exact_revision
                and event.get("query") == expected_query
                and event.get("state") in {"starting", "in_flight", "ready"}
                and event.get("benchmark_offset_from_commit_ms") is not None
                and math.isfinite(_number(event.get("benchmark_offset_from_commit_ms"), math.inf))
                and _number(event.get("benchmark_offset_from_commit_ms"), math.inf) <= 0
            ]
            retrieval_started = [
                event
                for event in row.get("trace_events", [])
                if isinstance(event, dict)
                and event.get("type") == "retrieval.started"
                and _integer(event.get("revision")) == exact_revision
                and event.get("query") == expected_query
                and event.get("benchmark_offset_from_commit_ms") is not None
                and math.isfinite(_number(event.get("benchmark_offset_from_commit_ms"), math.inf))
                and _number(event.get("benchmark_offset_from_commit_ms"), math.inf) <= 0
            ]
            if (
                len(exact_snapshots) != 1
                or exact_snapshots[0].get("transport_status") != "completed"
                or row.get("settled_final_snapshot_observed") is not True
                or not settled_events
                or not retrieval_started
                or not (
                    typing_duration
                    <= _number(
                        exact_snapshots[0].get("planned_offset_ms") if exact_snapshots else None
                    )
                    < planned
                )
            ):
                snapshot_contract_violations.append(key)
        cleanup = row.get("turn_cleanup")
        if not isinstance(cleanup, dict) or cleanup.get("turn_cleanup_status") != "not_required":
            cleanup_failures.append(key)
    if any("error" in row for row in rows):
        issues.append("one or more development outputs failed")
    if any(row.get("deadline_exceeded") is True for row in rows):
        issues.append("prediction rows include case-deadline failures")
    if provenance_mismatches:
        issues.append(f"prediction run/service provenance mismatch: {provenance_mismatches[:10]}")
    if malformed_timing:
        issues.append(
            f"prediction rows violate the typed-input timing contract: {malformed_timing[:10]}"
        )
    if drift_violations:
        issues.append(f"prediction rows exceed or misstate typing drift: {drift_violations[:10]}")
    if snapshot_contract_violations:
        issues.append(
            f"prediction rows violate path-specific snapshot scheduling: "
            f"{snapshot_contract_violations[:10]}"
        )
    if snapshot_transport_errors:
        issues.append(
            f"prediction rows record {snapshot_transport_errors} snapshot transport error(s)"
        )
    if cleanup_failures:
        issues.append(f"turn cleanup contract failed: {cleanup_failures[:10]}")

    warmup_gate = manifest.get("warmup_gate")
    expected_warmup_gate = {
        "status": "complete",
        "outputs_expected": 0,
        "outputs_completed": 0,
        "failures": 0,
        "cleanup_failures": 0,
        "retained_in_predictions": False,
    }
    if (
        _integer(manifest.get("warmup_outputs_expected")) != 0
        or _integer(manifest.get("warmup_outputs_completed")) != 0
        or _integer(manifest.get("warmup_failures")) != 0
        or warmup_gate != expected_warmup_gate
    ):
        issues.append("development warmup gate is invalid")
    timing_gate = manifest.get("timing_drift_gate")
    timing_gate = timing_gate if isinstance(timing_gate, dict) else {}
    max_observed_drift = max(observed_drifts, default=0.0)
    if (
        timing_gate.get("status") != "complete"
        or _integer(timing_gate.get("observations")) != len(rows)
        or _integer(timing_gate.get("violations")) != len(drift_violations)
        or not math.isclose(
            _number(timing_gate.get("max_allowed_abs_drift_ms")), 100.0, abs_tol=1e-6
        )
        or not math.isclose(
            _number(timing_gate.get("max_observed_abs_drift_ms")),
            max_observed_drift,
            abs_tol=1e-6,
        )
    ):
        issues.append("development timing-drift gate is invalid")
    snapshot_gate = manifest.get("snapshot_transport_gate")
    snapshot_gate = snapshot_gate if isinstance(snapshot_gate, dict) else {}
    if (
        snapshot_gate.get("status") != "complete"
        or _integer(snapshot_gate.get("errors")) != 0
        or snapshot_gate.get("async_sends_do_not_delay_commit") is not True
    ):
        issues.append("development snapshot-transport gate is invalid")
    deadline_gate = manifest.get("case_deadline_gate")
    deadline_gate = deadline_gate if isinstance(deadline_gate, dict) else {}
    if (
        deadline_gate.get("status") != "complete"
        or _integer(deadline_gate.get("deadline_failures")) != 0
        or not math.isclose(_number(deadline_gate.get("deadline_s")), 45.0, abs_tol=1e-6)
    ):
        issues.append("development case-deadline gate is invalid")
    cleanup_gate = manifest.get("turn_cleanup_gate")
    cleanup_gate = cleanup_gate if isinstance(cleanup_gate, dict) else {}
    if (
        cleanup_gate.get("status") != "complete"
        or _integer(cleanup_gate.get("cleanup_failures")) != len(cleanup_failures)
        or cleanup_gate.get("failure_turns_require_bounded_delete") is not True
    ):
        issues.append("development turn-cleanup gate is invalid")
    return {
        "status": "complete" if not issues else "invalid",
        "issues": issues,
        "manifest": manifest,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Development comparison — non-final",
        "",
        "> Uses checksummed development questions only. The unseen test split was not run.",
        "",
        f"Configuration: `{summary['configuration']['model']}` at "
        f"answer `{summary['configuration']['reasoning_effort']}`, trigger "
        f"`{summary['configuration']['trigger_reasoning_effort']}`, and summary "
        f"`{summary['configuration']['summary_reasoning_effort']}` reasoning with "
        f"`{summary['configuration']['embedding_model']}`.",
        "",
        "| Path | Automatic answer/alias proxy | Support+valid citation | Median TTFT | "
        "Median total | Usage-accounted model calls | Controllers | Retrievals | "
        "Dynamic function tools | "
        "Observed run cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for path, values in summary["paths"].items():
        cost_prefix = "" if values["cost_metric_status"] == "complete" else "≥"
        lines.append(
            f"| {path} | {values['expected_answer_accuracy'] * 100:.1f}% | "
            f"{values['supported_expected_answer_rate'] * 100:.1f}% | "
            f"{values['median_ttft_ms']:.0f} ms | {values['median_total_ms']:.0f} ms | "
            f"{values['usage_accounted_model_calls']:.0f} | "
            f"{values['total_controller_calls']} | "
            f"{values['total_retrieval_calls']} | {values['total_dynamic_tool_calls']} | "
            f"{cost_prefix}${values['observed_cost_usd']:.4f} |"
        )
    paired = summary["paired"]
    lines.extend(
        [
            "",
            "## Paired outcome",
            "",
            f"- Stream TTFT wins: {paired['stream_ttft_win_rate'] * 100:.1f}%",
            f"- Median Stream minus Naive TTFT: "
            f"{paired['median_stream_minus_naive_ttft_ms']:.0f} ms",
            f"- Median Stream minus Naive total: "
            f"{paired['median_stream_minus_naive_total_ms']:.0f} ms",
            f"- Mean Stream minus Naive automatic-proxy score: "
            f"{paired['mean_stream_minus_naive_accuracy'] * 100:.1f} percentage points",
            f"- Measured wall time: {summary['measured_wall_s']:.1f} s",
            "",
            "Dynamic function tools means model-issued `search_local_crag` calls after the",
            "shared primary retrieval. Controller and retrieval calls are reported separately.",
            "A ≥ cost is an observed lower bound because cancelled/timed-out requests may not",
            "return provider usage and are deliberately never estimated as zero.",
            "Human semantic-adjudication coverage is 0%; the automatic proxy is not a claim of",
            "100% semantic correctness.",
        ]
    )
    lines.extend(
        [
            "",
            "## Stabilization slices",
            "",
            "| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | "
            "Automatic-proxy delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, values in summary["stabilization_slices"].items():
        paired = values["paired"]
        lines.append(
            f"| {label} | {values['query_count']} | "
            f"{paired['stream_ttft_win_rate'] * 100:.1f}% | "
            f"{paired['median_stream_minus_naive_ttft_ms']:.0f} ms | "
            f"{paired['mean_stream_minus_naive_accuracy'] * 100:.1f} pp |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a non-final development A/B run")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_dev = read_jsonl(args.dev)
    dev_rows = [dict(row, _source_path=str(args.dev.resolve())) for row in source_dev]
    rows = read_jsonl(args.predictions)
    integrity = validate_dev_run(rows, dev_rows, args.predictions)
    if integrity["status"] != "complete":
        raise SystemExit("development run integrity failed: " + "; ".join(integrity["issues"]))
    manifest = integrity["manifest"]
    selected_count = int(manifest["query_count"])
    dev_rows = dev_rows[:selected_count]
    gold_by_id = {str(row["id"]): row for row in dev_rows}
    annotated = annotate(rows, gold_by_id)
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in annotated:
        by_path[str(row["path"])].append(row)
    statuses = manifest["data_status"]
    identity = statuses["naive"]
    summary = {
        "schema_version": 1,
        "status": "development_only_non_final",
        "unseen_test_run": False,
        "query_count": selected_count,
        "path_runs": len(rows),
        "measured_wall_s": float(manifest["completed_unix_s"] - manifest["created_unix_s"]),
        "configuration": {
            "model": identity["model"],
            "reasoning_effort": identity["reasoning_effort"],
            "trigger_reasoning_effort": identity.get(
                "trigger_reasoning_effort", identity["reasoning_effort"]
            ),
            "summary_reasoning_effort": identity.get(
                "summary_reasoning_effort", identity["reasoning_effort"]
            ),
            "embedding_model": identity["embedding_model"],
            "indexed_chunks": identity["indexed_chunks"],
        },
        "paths": {path: path_summary(items, gold_by_id) for path, items in sorted(by_path.items())},
        "paired": paired_summary(annotated),
        "stabilization_slices": stabilization_summaries(annotated, gold_by_id),
        "cost_metric_status": (
            "complete"
            if all(
                row.get("estimated_cost_usd", {}).get("accounting_complete") is True for row in rows
            )
            else "observed_lower_bound_non_final"
        ),
        "integrity": {"status": "complete", "issues": []},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
