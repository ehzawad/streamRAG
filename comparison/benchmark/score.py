#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from comparison.contracts import COMMON_IDENTITY_FIELDS, service_identity_issues

ROOT = Path(__file__).resolve().parents[2]
CITATION_RE = re.compile(r"\[([^\]\s]+::c\d{4})\]")
CLAUSE_SPLIT_RE = re.compile(r"[.!?;:\n]+|\bbut\b|\bhowever\b|,", re.IGNORECASE)
ABSTENTION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\binvalid question\b",
        r"\bfalse premise\b|\bpremise (?:is|was) false\b",
        r"\b(?:can not|cannot|can t|could not|unable to)"
        r"(?: [a-z0-9]+){0,4} (?:verify|identify|name|determine|compare|confirm|answer)\b",
        r"\b(?:insufficient|not enough) (?:evidence|information)(?: to [a-z0-9]+)?\b",
        r"\bno (?:available )?(?:evidence|score|source|information)\b",
        r"\b(?:evidence|corpus|sources?|documents?|pages?) (?:does|do|did) not "
        r"(?:establish|provide|support|verify|show|contain)\b",
        r"\bno [a-z0-9 ]{0,60} can be (?:identified|named|verified|determined)\b",
        r"\b(?:i|we) (?:can not|cannot|can t|could not) reliably\b",
    )
)
NON_ABSTENTION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bnot an invalid question\b",
        r"\bnot a false premise\b",
        r"\bno evidence problem\b",
        r"\bnot insufficient\b",
    )
)
ADJUDICATION_LABELS = {"perfect", "acceptable", "missing", "incorrect"}
FREEZE_DOMAIN = b"typed-streamrag-eval-freeze-v1\0"
RELATION_PAIRS = (
    ("smaller", "larger"),
    ("lower", "higher"),
    ("less", "more"),
    ("fewer", "more"),
    ("earlier", "later"),
    ("before", "after"),
    ("older", "younger"),
    ("shorter", "longer"),
    ("worse", "better"),
    ("worst", "best"),
    ("least", "most"),
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _integer(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _number(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return default


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_checksum_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
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
        if name in entries:
            raise ValueError(f"duplicate checksum manifest entry: {name}")
        entries[name] = digest
    return entries


def opaque_freeze_id(evaluation_manifest_sha256: str) -> str:
    return hashlib.sha256(FREEZE_DOMAIN + evaluation_manifest_sha256.encode("ascii")).hexdigest()


def resolve_manifest_path(manifest_path: Path, value: Any) -> Path:
    """Resolve a portable artifact path relative to its schema-v4 manifest."""

    raw = Path(str(value or ""))
    if raw.is_absolute():
        raise ValueError("schema v4 artifact paths must be manifest-relative")
    return (manifest_path.parent / raw).resolve()


def validate_run_integrity(
    rows: list[dict[str, Any]],
    predictions_path: Path,
    manifest_path: Path,
    gold_path: Path,
    evaluation_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Offline-bind gold to a gold-blind run and enforce reportability gates."""

    issues: list[str] = []
    if not manifest_path.is_file():
        return {
            "status": "missing_or_incomplete",
            "manifest": manifest_path.name,
            "issues": ["adjacent benchmark manifest is missing"],
        }
    try:
        manifest = read_manifest(manifest_path)
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "status": "missing_or_incomplete",
            "manifest": manifest_path.name,
            "issues": [f"benchmark manifest is unreadable: {exc}"],
        }

    if any(key in manifest for key in ("gold", "gold_sha256")):
        issues.append("runner manifest improperly contains scorer-only gold identity")
    if manifest.get("smoke_non_reportable") is not False:
        issues.append("manifest marks the run smoke/non-reportable")
    if manifest.get("distinct_backend_instances") is not True:
        issues.append("manifest does not prove distinct backend instances")
    if manifest.get("finalized") is not True:
        issues.append("manifest was not finalized after prediction generation")
    if manifest.get("reportable") is not True:
        issues.append("runner reportability gate is not complete")
    if manifest.get("run_status") != "completed_reportable":
        issues.append("runner did not finish with completed_reportable status")

    repetitions = int(manifest.get("repetitions") or 0)
    warmup_repetitions = int(manifest.get("warmup_repetitions") or 0)
    if repetitions != 1 or warmup_repetitions != 0:
        issues.append("reportable protocol requires no warm-up and one measured repetition")
    protocol_gate = manifest.get("preregistered_protocol_gate")
    required_protocol = {
        "status": "complete",
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
    if protocol_gate != required_protocol:
        issues.append("manifest preregistered protocol contract is invalid")
    if (
        float(manifest.get("words_per_minute") or -1) != 70.0
        or float(manifest.get("post_typing_dwell_ms") or -1) != 5000.0
        or float(manifest.get("settled_draft_delay_ms") or -1) != 500.0
        or float(manifest.get("max_typing_drift_ms") or -1) != 100.0
        or float(manifest.get("case_deadline_s") or -1) != 45.0
    ):
        issues.append("manifest typed-input timing contract is invalid")
    for gate_name in (
        "preregistered_protocol_gate",
        "warmup_gate",
        "timing_drift_gate",
        "snapshot_transport_gate",
        "case_deadline_gate",
        "turn_cleanup_gate",
    ):
        gate = manifest.get(gate_name)
        if not isinstance(gate, dict) or gate.get("status") != "complete":
            issues.append(f"manifest {gate_name} is not complete")

    if manifest.get("schema_version") != 4:
        issues.append("benchmark manifest schema_version must be 4")
    try:
        manifest_predictions = resolve_manifest_path(manifest_path, manifest.get("predictions"))
    except ValueError as exc:
        manifest_predictions = Path()
        issues.append(str(exc))
    if not manifest_predictions.is_file():
        issues.append("manifest prediction file is missing")
    elif manifest_predictions.resolve() != predictions_path.resolve():
        issues.append("manifest prediction path does not match the scored file")
    manifest_predictions_hash = str(manifest.get("predictions_sha256") or "")
    if (
        not predictions_path.is_file()
        or not manifest_predictions_hash
        or sha256_file(predictions_path) != manifest_predictions_hash
    ):
        issues.append("prediction checksum does not match finalized manifest")
    harness_hashes = manifest.get("benchmark_harness_sha256")
    expected_harness_hashes = {
        "run_benchmark.py": sha256_file(ROOT / "comparison" / "benchmark" / "run_benchmark.py"),
        "typed_trace.py": sha256_file(ROOT / "comparison" / "benchmark" / "typed_trace.py"),
    }
    if harness_hashes != expected_harness_hashes:
        issues.append("benchmark harness source does not match the content-addressed run")

    statuses = manifest.get("data_status")
    statuses = statuses if isinstance(statuses, dict) else {}
    required_identity_fields = set(COMMON_IDENTITY_FIELDS)
    compared_fields = set(manifest.get("compared_status_fields") or [])
    if not required_identity_fields <= compared_fields:
        issues.append("manifest does not compare every required model/data/index identity")
    for path in ("naive", "stream"):
        status = statuses.get(path)
        if not isinstance(status, dict) or status.get("approval_status") != "approved_frozen":
            issues.append(f"{path} status is not approved_frozen")
        elif any(status.get(field) is None for field in required_identity_fields):
            issues.append(f"{path} status is missing required model/data/index identities")
        else:
            identity_issues = service_identity_issues(status, path, root=ROOT)
            issues.extend(f"{path} {issue}" for issue in identity_issues)
        if isinstance(status, dict) and (
            status.get("dataset_checksums_valid") is not True
            or status.get("index_metadata_ready") is not True
            or status.get("index_matches_current_corpus") is not True
            or _integer(status.get("indexed_chunks"), 0) <= 0
            or _integer(status.get("indexed_chunks"), 0)
            != _integer(status.get("indexed_desired_chunks"))
            or status.get("index_source_sha256") != status.get("current_index_source_sha256")
        ):
            issues.append(f"{path} status does not prove a complete current index")
    if all(isinstance(statuses.get(path), dict) for path in ("naive", "stream")):
        identity_mismatches = [
            field
            for field in required_identity_fields
            if statuses["naive"].get(field) != statuses["stream"].get(field)
        ]
        if identity_mismatches:
            issues.append(f"backend identity mismatch: {sorted(identity_mismatches)}")
    path_urls = manifest.get("path_urls")
    path_urls = path_urls if isinstance(path_urls, dict) else {}
    instance_ids = manifest.get("backend_instance_ids")
    instance_ids = instance_ids if isinstance(instance_ids, dict) else {}
    if (
        not str(path_urls.get("naive") or "")
        or not str(path_urls.get("stream") or "")
        or str(path_urls.get("naive")).rstrip("/") == str(path_urls.get("stream")).rstrip("/")
    ):
        issues.append("manifest does not bind distinct service URLs")
    if (
        not str(instance_ids.get("naive") or "")
        or not str(instance_ids.get("stream") or "")
        or instance_ids.get("naive") == instance_ids.get("stream")
        or any(
            isinstance(statuses.get(path), dict)
            and statuses[path].get("instance_id") != instance_ids.get(path)
            for path in ("naive", "stream")
        )
    ):
        issues.append("manifest does not bind two matching, distinct backend instances")

    try:
        query_path = resolve_manifest_path(manifest_path, manifest.get("queries"))
    except ValueError as exc:
        query_path = Path()
        issues.append(str(exc))
    query_rows: list[dict[str, Any]] = []
    if not query_path.is_file():
        issues.append("manifest query file is missing")
    else:
        expected_query_hash = str(manifest.get("queries_sha256") or "")
        if not expected_query_hash or sha256_file(query_path) != expected_query_hash:
            issues.append("query file checksum does not match manifest")
        query_rows = read_jsonl(query_path)

    query_ids = [str(value) for value in (manifest.get("query_ids") or [])]
    if not query_ids:
        issues.append("manifest query_ids are missing")
    if len(query_ids) != int(manifest.get("query_count") or 0):
        issues.append("manifest query_count does not match query_ids")
    if len(query_ids) != 10 or int(manifest.get("query_count") or 0) != 10:
        issues.append("reportable scoring requires exactly 10 frozen query IDs")
    if len(query_ids) != len(set(query_ids)):
        issues.append("manifest query_ids contain duplicates")

    query_by_id = {str(row.get("id")): row for row in query_rows}
    if len(query_by_id) != len(query_rows):
        issues.append("checksummed query file contains duplicate IDs")
    file_query_ids = [str(row.get("id")) for row in query_rows]
    query_selection = manifest.get("query_selection")
    expected_query_selection = {
        "method": "ordered_prefix",
        "source_query_count": len(file_query_ids),
        "selected_query_count": 10,
    }
    if query_selection != expected_query_selection:
        issues.append("manifest frozen-query selection contract is invalid")
    if query_ids != file_query_ids[:10]:
        issues.append(
            "manifest query_ids do not equal the ordered first 10 checksummed query-file IDs"
        )

    expected_keys = {
        (query_id, repetition, path)
        for query_id in query_ids
        for repetition in range(1, repetitions + 1)
        for path in ("naive", "stream")
    }
    if len(expected_keys) != 20:
        issues.append("reportable scoring requires exactly 20 total path runs")
    observed_keys = [
        (str(row.get("id")), int(row.get("repetition") or 0), str(row.get("path"))) for row in rows
    ]
    observed_counts = Counter(observed_keys)
    duplicate_keys = [key for key, count in observed_counts.items() if count > 1]
    observed_set = set(observed_keys)
    missing_keys = sorted(expected_keys - observed_set)
    extra_keys = sorted(observed_set - expected_keys)
    if duplicate_keys:
        issues.append(f"duplicate prediction keys: {duplicate_keys[:10]}")
    if missing_keys:
        issues.append(f"missing prediction keys: {missing_keys[:10]}")
    if extra_keys:
        issues.append(f"unexpected prediction keys: {extra_keys[:10]}")
    if len(rows) != len(expected_keys):
        issues.append(
            f"prediction row count {len(rows)} does not equal expected {len(expected_keys)}"
        )
    if int(manifest.get("prediction_rows_expected") or 0) != len(expected_keys):
        issues.append("manifest prediction_rows_expected is inconsistent")
    if int(manifest.get("prediction_rows_observed") or 0) != len(rows):
        issues.append("manifest prediction_rows_observed is inconsistent")
    expected_warmup_outputs = len(query_ids) * warmup_repetitions * 2
    if int(manifest.get("warmup_outputs_expected") or 0) != expected_warmup_outputs:
        issues.append("manifest warmup_outputs_expected is inconsistent")
    if int(manifest.get("warmup_outputs_completed") or 0) != expected_warmup_outputs:
        issues.append("manifest warm-up output count is inconsistent")
    if int(manifest.get("warmup_failures") or 0) != 0:
        issues.append("manifest records warm-up failures")

    query_mismatches = []
    for row in rows:
        source = query_by_id.get(str(row.get("id")))
        if source and (
            row.get("query") != source.get("query")
            or row.get("query_time") != source.get("query_time")
        ):
            query_mismatches.append(str(row.get("id")))
    if query_mismatches:
        issues.append(f"prediction query payload mismatch: {sorted(set(query_mismatches))[:10]}")

    run_tag = str(manifest.get("run_tag") or "")
    provenance_mismatches = []
    for row in rows:
        path = str(row.get("path"))
        expected_scope = f"bench-{run_tag}-{int(row.get('repetition') or 0)}-{row.get('id')}-{path}"
        expected_url = str(path_urls.get(path) or "").rstrip("/")
        if (
            not run_tag
            or row.get("session_scope") != expected_scope
            or not expected_url
            or str(row.get("service_base_url") or "").rstrip("/") != expected_url
        ):
            provenance_mismatches.append((str(row.get("id")), path))
    if provenance_mismatches:
        issues.append(f"prediction run/service provenance mismatch: {provenance_mismatches[:10]}")

    completed_rows = [row for row in rows if "error" not in row]
    failed_rows = [row for row in rows if "error" in row]
    if failed_rows:
        issues.append(f"prediction file contains {len(failed_rows)} counted failure row(s)")
    max_typing_drift_ms = float(manifest.get("max_typing_drift_ms") or -1)
    drift_violations: list[tuple[str, int, str]] = []
    malformed_timing: list[tuple[str, int, str]] = []
    malformed_dwell: list[tuple[str, int, str]] = []
    malformed_final_snapshots: list[tuple[str, int, str]] = []
    snapshot_transport_errors = 0
    for row in completed_rows:
        key = (str(row.get("id")), int(row.get("repetition") or 0), str(row.get("path")))
        typing = row.get("typing")
        if not isinstance(typing, dict):
            malformed_timing.append(key)
            continue
        try:
            planned = float(typing["planned_commit_offset_ms"])
            actual = float(typing["actual_commit_offset_ms"])
            recorded_drift = float(typing["commit_drift_ms"])
            row_tolerance = float(typing["max_allowed_drift_ms"])
            typing_duration = float(typing["typing_duration_ms"])
            dwell = float(typing["post_typing_dwell_ms"])
            simulated_duration = float(typing["simulated_duration_ms"])
            words_per_minute = float(typing["words_per_minute"])
            snapshot_interval_ms = int(typing["snapshot_interval_ms"])
            settled_draft_delay_ms = int(typing["settled_draft_delay_ms"])
        except KeyError, TypeError, ValueError:
            malformed_timing.append(key)
            continue
        if (
            max_typing_drift_ms < 0
            or abs((actual - planned) - recorded_drift) > 1.0
            or abs(row_tolerance - max_typing_drift_ms) > 1e-6
            or typing.get("drift_within_tolerance") is not True
            or abs(recorded_drift) > max_typing_drift_ms
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
            malformed_dwell.append(key)
        row_snapshot_errors = int(row.get("snapshot_transport_errors") or 0)
        snapshot_transport_errors += row_snapshot_errors
        schedules = row.get("snapshot_schedule")
        if not isinstance(schedules, list) or any(
            not isinstance(schedule, dict)
            or schedule.get("transport_status") not in {"completed", "aborted_at_commit"}
            for schedule in schedules
        ):
            snapshot_transport_errors += 1
        elif row.get("path") == "naive" and schedules:
            malformed_final_snapshots.append(key)
        elif row.get("path") == "stream":
            query = str(row.get("query") or "").strip()
            exact_snapshots = []
            for schedule in schedules:
                try:
                    character_count = int(schedule.get("character_count") or -1)
                except TypeError, ValueError:
                    continue
                if schedule.get("is_final") is True and character_count == len(query):
                    exact_snapshots.append(schedule)
            if (
                len(exact_snapshots) != 1
                or exact_snapshots[0].get("transport_status") != "completed"
                or row.get("settled_final_snapshot_observed") is not True
            ):
                malformed_final_snapshots.append(key)
            else:
                try:
                    final_offset = float(exact_snapshots[0].get("planned_offset_ms") or -1)
                except TypeError, ValueError:
                    final_offset = -1
                if not typing_duration <= final_offset < planned:
                    malformed_final_snapshots.append(key)
                exact_revision = _integer(exact_snapshots[0].get("revision"))
                expected_query = str(row.get("query") or "").strip()
                settled = any(
                    isinstance(event, dict)
                    and event.get("type") == "draft.settled"
                    and _integer(event.get("revision")) == exact_revision
                    and event.get("query") == expected_query
                    and event.get("state") in {"starting", "in_flight", "ready"}
                    and event.get("benchmark_offset_from_commit_ms") is not None
                    and math.isfinite(
                        _number(event.get("benchmark_offset_from_commit_ms"), math.inf)
                    )
                    and _number(event.get("benchmark_offset_from_commit_ms"), math.inf) <= 0
                    for event in row.get("trace_events", [])
                )
                retrieval_started = any(
                    isinstance(event, dict)
                    and event.get("type") == "retrieval.started"
                    and _integer(event.get("revision")) == exact_revision
                    and event.get("query") == expected_query
                    and event.get("benchmark_offset_from_commit_ms") is not None
                    and math.isfinite(
                        _number(event.get("benchmark_offset_from_commit_ms"), math.inf)
                    )
                    and _number(event.get("benchmark_offset_from_commit_ms"), math.inf) <= 0
                    for event in row.get("trace_events", [])
                )
                if not settled or not retrieval_started:
                    malformed_final_snapshots.append(key)
    if malformed_timing:
        issues.append(f"prediction rows have malformed typing timing: {malformed_timing[:10]}")
    if drift_violations:
        issues.append(f"prediction rows exceed or misstate typing drift: {drift_violations[:10]}")
    if malformed_dwell:
        issues.append(
            f"prediction rows violate the fixed typing/dwell contract: {malformed_dwell[:10]}"
        )
    if malformed_final_snapshots:
        issues.append(
            "prediction rows violate path-specific snapshot scheduling: "
            f"{malformed_final_snapshots[:10]}"
        )
    if snapshot_transport_errors:
        issues.append(
            f"prediction rows record {snapshot_transport_errors} snapshot transport error(s)"
        )
    if any(row.get("deadline_exceeded") is True for row in rows):
        issues.append("prediction rows include case-deadline failures")
    cleanup_failures = []
    for row in rows:
        cleanup = row.get("turn_cleanup")
        cleanup = cleanup if isinstance(cleanup, dict) else {}
        expected_cleanup_status = "completed" if "error" in row else "not_required"
        if cleanup.get("turn_cleanup_status") != expected_cleanup_status:
            cleanup_failures.append(
                (str(row.get("id")), int(row.get("repetition") or 0), str(row.get("path")))
            )
    if cleanup_failures:
        issues.append(f"turn cleanup contract failed: {cleanup_failures[:10]}")
    timing_gate = manifest.get("timing_drift_gate")
    if isinstance(timing_gate, dict):
        if int(timing_gate.get("observations") or 0) != len(completed_rows):
            issues.append("manifest typing-drift observation count is inconsistent")
        if int(timing_gate.get("violations") or 0) != len(drift_violations):
            issues.append("manifest typing-drift violation count is inconsistent")
    snapshot_gate = manifest.get("snapshot_transport_gate")
    if isinstance(snapshot_gate, dict) and int(snapshot_gate.get("errors") or 0) != 0:
        issues.append("manifest snapshot transport gate records errors")
    deadline_gate = manifest.get("case_deadline_gate")
    if isinstance(deadline_gate, dict) and int(deadline_gate.get("deadline_failures") or 0) != 0:
        issues.append("manifest case deadline gate records failures")
    cleanup_gate = manifest.get("turn_cleanup_gate")
    if isinstance(cleanup_gate, dict) and int(cleanup_gate.get("cleanup_failures") or 0) != len(
        cleanup_failures
    ):
        issues.append("manifest turn-cleanup failure count is inconsistent")

    try:
        serving_manifest_path = resolve_manifest_path(
            manifest_path, manifest.get("serving_checksum_manifest")
        )
    except ValueError as exc:
        serving_manifest_path = Path()
        issues.append(str(exc))
    try:
        serving_manifest_sha256 = sha256_file(serving_manifest_path)
        serving_checksums = read_checksum_manifest(serving_manifest_path)
    except (OSError, ValueError) as exc:
        serving_manifest_sha256 = ""
        serving_checksums = {}
        issues.append(f"serving checksum manifest is unavailable or invalid: {exc}")
    if serving_manifest_sha256 != str(manifest.get("serving_checksum_manifest_sha256") or ""):
        issues.append("serving checksum manifest hash does not match the run")
    document_entries = set(serving_checksums) & {
        "documents.jsonl",
        "documents.jsonl.bz2",
    }
    expected_serving_entries = {
        "dataset_summary.json",
        "test_queries.jsonl",
        "inference_bundle.json",
    } | document_entries
    if len(document_entries) != 1 or set(serving_checksums) != expected_serving_entries:
        issues.append("serving checksum manifest is not the exact gold-free bundle")
    if any("gold" in name.casefold() for name in serving_checksums):
        issues.append("serving checksum manifest improperly names scorer-only gold")
    if serving_checksums.get("test_queries.jsonl") != str(manifest.get("queries_sha256") or ""):
        issues.append("serving bundle query hash does not match the run")

    evaluation_manifest_path = evaluation_manifest_path or gold_path.parent / "checksums.sha256"
    evaluation_manifest_sha256 = ""
    evaluation_checksums: dict[str, str] = {}
    try:
        evaluation_manifest_sha256 = sha256_file(evaluation_manifest_path)
        evaluation_checksums = read_checksum_manifest(evaluation_manifest_path)
    except (OSError, ValueError) as exc:
        issues.append(f"evaluation checksum manifest is unavailable or invalid: {exc}")
    runner_evaluation_manifest_sha256 = str(manifest.get("evaluation_manifest_sha256") or "")
    runner_freeze_id = str(manifest.get("freeze_id") or "")
    if (
        not evaluation_manifest_sha256
        or runner_evaluation_manifest_sha256 != evaluation_manifest_sha256
    ):
        issues.append("offline evaluation manifest does not match the runner freeze binding")
    if not runner_freeze_id or runner_freeze_id != opaque_freeze_id(
        runner_evaluation_manifest_sha256
    ):
        issues.append("runner opaque freeze_id is invalid")

    full_query_hash = evaluation_checksums.get("test_queries.jsonl")
    if not full_query_hash or full_query_hash != str(manifest.get("queries_sha256") or ""):
        issues.append("frozen evaluation query hash does not match the runner query bundle")
    frozen_gold_hash = evaluation_checksums.get("test_gold.jsonl")
    if not gold_path.is_file():
        issues.append("scorer gold file is missing")
    elif not frozen_gold_hash or sha256_file(gold_path) != frozen_gold_hash:
        issues.append("scorer gold checksum does not match the frozen evaluation manifest")
    else:
        gold_ids = [str(row.get("id")) for row in read_jsonl(gold_path)]
        if len(gold_ids) != len(set(gold_ids)):
            issues.append("checksummed scorer gold contains duplicate IDs")
        if gold_ids != file_query_ids:
            issues.append("checksummed scorer gold IDs do not equal frozen query-file IDs")

    return {
        "status": "complete" if not issues else "missing_or_incomplete",
        "manifest": manifest_path.name,
        "predictions": predictions_path.name,
        "queries": str(manifest.get("queries") or ""),
        "gold": gold_path.name,
        "gold_sha256": frozen_gold_hash,
        "evaluation_manifest": evaluation_manifest_path.name,
        "evaluation_manifest_sha256": evaluation_manifest_sha256,
        "freeze_id": runner_freeze_id,
        "expected_prediction_rows": len(expected_keys),
        "observed_prediction_rows": len(rows),
        "duplicate_keys": len(duplicate_keys),
        "missing_keys": len(missing_keys),
        "extra_keys": len(extra_keys),
        "issues": issues,
    }


def read_adjudications(path: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    adjudications: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in read_jsonl(path):
        label = str(row.get("label", ""))
        if label not in ADJUDICATION_LABELS:
            raise ValueError(f"invalid adjudication label: {label!r}")
        if not str(row.get("reviewer", "")).strip():
            raise ValueError("manual adjudication requires a reviewer")
        prediction_digest = str(row.get("prediction_sha256", ""))
        if len(prediction_digest) != 64 or any(
            character not in "0123456789abcdef" for character in prediction_digest
        ):
            raise ValueError("manual adjudication requires prediction_sha256")
        key = (str(row["id"]), int(row["repetition"]), str(row["path"]))
        if key in adjudications:
            raise ValueError(f"duplicate adjudication: {key}")
        adjudications[key] = row
    return adjudications


def prediction_sha256(row: dict[str, Any]) -> str:
    """Bind a human label to the exact prediction row that was reviewed."""

    payload = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_adjudication_integrity(
    rows: list[dict[str, Any]],
    adjudications: dict[tuple[str, int, str], dict[str, Any]],
    path: Path | None,
) -> dict[str, Any]:
    expected_keys = {
        (str(row.get("id")), int(row.get("repetition") or 0), str(row.get("path")))
        for row in rows
        if "error" not in row
    }
    observed_keys = set(adjudications)
    missing = sorted(expected_keys - observed_keys)
    extra = sorted(observed_keys - expected_keys)
    rows_by_key = {
        (str(row.get("id")), int(row.get("repetition") or 0), str(row.get("path"))): row
        for row in rows
        if "error" not in row
    }
    mismatched_predictions = sorted(
        key
        for key in expected_keys & observed_keys
        if str(adjudications[key].get("prediction_sha256")) != prediction_sha256(rows_by_key[key])
    )
    issues: list[str] = []
    resolved_path: str | None = None
    digest: str | None = None
    if path is None:
        return {
            "status": "not_requested",
            "path": None,
            "sha256": None,
            "expected_keys": len(expected_keys),
            "observed_keys": 0,
            "missing_keys": len(expected_keys),
            "extra_keys": 0,
            "issues": [],
        }
    elif not path.is_file():
        issues.append("manual adjudication file is missing")
        resolved_path = path.name
    else:
        resolved_path = path.name
        digest = sha256_file(path)
    if missing:
        issues.append(f"missing adjudication keys: {missing[:10]}")
    if extra:
        issues.append(f"unexpected adjudication keys: {extra[:10]}")
    if mismatched_predictions:
        issues.append(
            f"adjudications do not match current prediction rows: {mismatched_predictions[:10]}"
        )
    return {
        "status": "complete" if not issues else "missing_or_incomplete",
        "path": resolved_path,
        "sha256": digest,
        "expected_keys": len(expected_keys),
        "observed_keys": len(observed_keys),
        "missing_keys": len(missing),
        "extra_keys": len(extra),
        "mismatched_prediction_hashes": len(mismatched_predictions),
        "issues": issues,
    }


def automatic_completeness(
    *,
    failures: int,
    run_integrity: dict[str, Any],
) -> bool:
    return failures == 0 and run_integrity.get("status") == "complete"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold().replace(",", "")
    return " ".join(re.sub(r"[^a-z0-9.%]+", " ", value).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def abstention_detected(answer: str) -> bool:
    for raw_clause in CLAUSE_SPLIT_RE.split(answer):
        clause = normalize(raw_clause)
        if not clause or any(pattern.search(clause) for pattern in NON_ABSTENTION_PATTERNS):
            continue
        if any(pattern.search(clause) for pattern in ABSTENTION_PATTERNS):
            return True
    return False


def false_premise_rejection_detected(answer: str) -> bool:
    """Accept an abstention or an explicit correction of a false premise."""

    if abstention_detected(answer):
        return True
    normalized = normalize(answer)
    return (
        bool(re.match(r"^no(?:\s|[.%]|$)", normalized))
        or normalized.startswith("that premise is incorrect ")
        or normalized.startswith("the premise is incorrect ")
    )


def relation_contradicted(clause: str, question: str) -> bool:
    """Reject a candidate asserted with the opposite queried relation."""

    normalized_question = normalize(question)
    for first, second in RELATION_PAIRS:
        first_asked = _contains_phrase(normalized_question, first)
        second_asked = _contains_phrase(normalized_question, second)
        if first_asked == second_asked:
            continue
        expected, opposite = (first, second) if first_asked else (second, first)
        expected_negated = re.search(
            rf"\b(?:no|not|never)\b(?:\s+[a-z0-9]+){{0,2}}\s+{re.escape(expected)}\b",
            clause,
        )
        if expected_negated:
            return True
        if _contains_phrase(clause, opposite) and not _contains_phrase(clause, expected):
            return True
    return False


def candidate_asserted(answer: str, candidate: str, question: str = "") -> bool:
    expected = normalize(candidate)
    if not expected:
        return False
    escaped = re.escape(expected)
    for raw_clause in CLAUSE_SPLIT_RE.split(answer):
        clause = normalize(raw_clause)
        if not _contains_phrase(clause, expected) or abstention_detected(raw_clause):
            continue
        negated_before = re.search(
            rf"\b(?:no|not|never|cannot|can t|can not|could not)"
            rf"(?: [a-z0-9]+){{0,6}} {escaped}\b",
            clause,
        )
        negated_after = re.search(
            rf"\b{escaped}\b(?: [a-z0-9]+){{0,3}} not\b",
            clause,
        )
        mere_mention = re.search(
            rf"\b(?:evidence|corpus|question|sources?|documents?|pages?) "
            rf"(?:only )?(?:mentions?|contains?|lists?|references?)"
            rf"(?: [a-z0-9]+){{0,4}} {escaped}\b",
            clause,
        )
        if (
            not negated_before
            and not negated_after
            and not mere_mention
            and not relation_contradicted(clause, question)
        ):
            return True
    return False


def expected_hit(answer: str, gold: dict[str, Any], question: str = "") -> bool:
    is_false_premise = normalize(gold["answer"]) == "invalid question"
    if is_false_premise:
        return false_premise_rejection_detected(answer)
    candidates = [gold["answer"], *gold.get("alt_answers", [])]
    for candidate in candidates:
        parts = [part for part in candidate.split(",") if normalize(part)]
        if len(parts) > 1 and all(candidate_asserted(answer, part, question) for part in parts):
            return True
        if candidate_asserted(answer, candidate, question):
            return True
    return False


def cited_chunk_ids(answer: str) -> set[str]:
    return {match.group(1) for match in CITATION_RE.finditer(answer)}


def cited_doc_ids(chunk_ids: set[str]) -> set[str]:
    return {chunk_id.rsplit("::c", 1)[0] for chunk_id in chunk_ids}


def percentile(values: list[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = fraction * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def stdev(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else None


def word_bucket(row: dict[str, Any]) -> str:
    count = int(row.get("word_count") or len(str(row.get("query", "")).split()))
    if count <= 8:
        return "short_1_8"
    if count <= 12:
        return "medium_9_12"
    return "long_13_plus"


def annotate(
    rows: list[dict[str, Any]],
    gold_by_id: dict[str, dict[str, Any]],
    adjudications: dict[tuple[str, int, str], dict[str, Any]] | None = None,
) -> list[dict]:
    adjudications = adjudications or {}
    annotated = []
    for raw in rows:
        row = dict(raw)
        answer = str(row.get("answer", ""))
        gold = gold_by_id.get(str(row.get("id", "")))
        row["abstained"] = "error" not in row and abstention_detected(answer)
        row["false_premise_rejected"] = "error" not in row and false_premise_rejection_detected(
            answer
        )
        row["expected_hit"] = bool(
            gold and "error" not in row and expected_hit(answer, gold, str(row.get("query", "")))
        )
        cited_chunks = cited_chunk_ids(answer)
        source_chunks = {
            str(source.get("chunk_id"))
            for source in row.get("sources", [])
            if source.get("chunk_id")
        }
        valid_chunks = cited_chunks & source_chunks
        row["cited_chunk_ids"] = sorted(cited_chunks)
        row["valid_cited_chunk_ids"] = sorted(valid_chunks)
        row["invalid_cited_chunk_ids"] = sorted(cited_chunks - source_chunks)
        row["cited_doc_ids"] = sorted(cited_doc_ids(valid_chunks))
        row["has_citation_marker"] = bool(cited_chunks)
        row["has_valid_citation"] = bool(valid_chunks)
        row["cited_expected_hit"] = row["expected_hit"] and row["has_valid_citation"]
        supporting = set(gold.get("supporting_doc_ids", []) if gold else [])
        supporting.update(gold.get("acceptable_supporting_doc_ids", []) if gold else [])
        row["support_evaluable"] = bool(supporting)
        row["cites_supporting_doc"] = (
            bool(set(row["cited_doc_ids"]) & supporting) if supporting else None
        )
        row["supported_expected_hit"] = (
            row["expected_hit"] and row["cites_supporting_doc"] if supporting else None
        )
        adjudication = adjudications.get(
            (str(row.get("id", "")), int(row.get("repetition", 0)), str(row.get("path", "")))
        )
        row["manual_adjudication"] = (
            adjudication.get("label")
            if adjudication and adjudication.get("prediction_sha256") == prediction_sha256(raw)
            else None
        )
        row["word_count_bucket"] = word_bucket(row)
        annotated.append(row)
    return annotated


def path_summary(items: list[dict[str, Any]], gold_by_id: dict[str, dict[str, Any]]) -> dict:
    completed = [item for item in items if "error" not in item]
    ttft = [
        float(item["timing"]["submit_to_first_token_ms"])
        for item in completed
        if item.get("timing", {}).get("submit_to_first_token_ms") is not None
    ]
    totals = [float(item["timing"]["total_response_ms"]) for item in completed]
    retrieval = [float(item["timing"].get("retrieval_ms") or 0) for item in completed]
    lead = [
        float(item["timing"].get("accepted_retrieval_lead_at_commit_ms") or 0) for item in completed
    ]
    positive_lead = [value for value in lead if value > 0]
    candidate_lead = [
        float(item["timing"].get("accepted_candidate_retrieval_lead_ms") or 0) for item in completed
    ]
    false_premise = [
        item
        for item in items
        if gold_by_id.get(item["id"], {}).get("question_type") == "false_premise"
        or item.get("question_type") == "false_premise"
    ]
    false_premise_ids = {item["id"] for item in false_premise}
    answerable = [item for item in items if item["id"] not in false_premise_ids]
    costs = [float(item["estimated_cost_usd"]["total"]) for item in completed]
    accounting_complete = [
        item for item in completed if item["estimated_cost_usd"].get("accounting_complete") is True
    ]
    unpriced_controller_calls = sum(
        int(item["estimated_cost_usd"].get("unpriced_cancelled_controller_calls") or 0)
        for item in completed
    )
    unpriced_retrieval_calls = sum(
        int(item["estimated_cost_usd"].get("unpriced_cancelled_retrieval_calls") or 0)
        for item in completed
    )
    unpriced_controller_timeouts = sum(
        int(item["estimated_cost_usd"].get("unpriced_controller_timeout_calls") or 0)
        for item in completed
    )
    unpriced_controller_failures = sum(
        int(item["estimated_cost_usd"].get("unpriced_controller_failure_calls") or 0)
        for item in completed
    )
    unpriced_retrieval_timeouts = sum(
        int(item["estimated_cost_usd"].get("unpriced_retrieval_timeout_calls") or 0)
        for item in completed
    )
    unpriced_retrieval_failures = sum(
        int(item["estimated_cost_usd"].get("unpriced_retrieval_failure_calls") or 0)
        for item in completed
    )
    unpriced_local_tool_calls = sum(
        int(item["estimated_cost_usd"].get("unpriced_local_tool_calls") or 0) for item in completed
    )
    calls = [float(item["usage"]["calls"]) for item in completed]
    controller_calls = [int(item.get("controller", {}).get("calls") or 0) for item in completed]
    retrieval_calls = [int(item.get("retrieval", {}).get("calls") or 0) for item in completed]
    dynamic_tool_calls = [len(item.get("tool_traces", [])) for item in completed]
    completed_dynamic_tool_calls = [
        sum(trace.get("status") == "completed" for trace in item.get("tool_traces", []))
        for item in completed
    ]
    post_commit_wall = [float(item.get("post_commit_wall_ms", 0)) for item in completed]
    diagnostics = [item.get("diagnostics", {}) for item in completed]
    cache_known = [item for item in diagnostics if item.get("cache_status") != "unknown"]
    support_evaluable = [item for item in items if item["support_evaluable"]]
    manually_adjudicated = [item for item in completed if item["manual_adjudication"]]
    manual_counts = {
        label: sum(item["manual_adjudication"] == label for item in manually_adjudicated)
        for label in sorted(ADJUDICATION_LABELS)
    }
    return {
        "outputs": len(items),
        "completed": len(completed),
        "failures": len(items) - len(completed),
        "completion_rate": len(completed) / len(items) if items else None,
        "unique_queries": len({item["id"] for item in items}),
        "expected_answer_accuracy": (
            sum(bool(item["expected_hit"]) for item in items) / len(items) if items else None
        ),
        "citation_marker_rate": (
            sum(bool(item["has_citation_marker"]) for item in items) / len(items) if items else None
        ),
        "valid_citation_rate": (
            sum(bool(item["has_valid_citation"]) for item in items) / len(items) if items else None
        ),
        "cited_expected_answer_rate": (
            sum(bool(item["cited_expected_hit"]) for item in items) / len(items) if items else None
        ),
        "support_evaluable_outputs": len(support_evaluable),
        "supporting_doc_citation_rate": (
            sum(bool(item["cites_supporting_doc"]) for item in support_evaluable)
            / len(support_evaluable)
            if support_evaluable
            else None
        ),
        "supported_expected_answer_rate": (
            sum(bool(item["supported_expected_hit"]) for item in support_evaluable)
            / len(support_evaluable)
            if support_evaluable
            else None
        ),
        "manual_adjudication": {
            "completed_outputs": len(completed),
            "adjudicated_outputs": len(manually_adjudicated),
            "coverage": len(manually_adjudicated) / len(completed) if completed else None,
            "labels": manual_counts,
            "perfect_or_acceptable_rate": (
                (manual_counts["perfect"] + manual_counts["acceptable"]) / len(manually_adjudicated)
                if manually_adjudicated
                else None
            ),
        },
        "false_premise_rejection_rate": (
            sum(bool(item["false_premise_rejected"]) for item in false_premise) / len(false_premise)
            if false_premise
            else None
        ),
        "answerable_abstention_rate": (
            sum(bool(item["abstained"]) for item in answerable) / len(answerable)
            if answerable
            else None
        ),
        "median_ttft_ms": median(ttft),
        "mean_ttft_ms": mean(ttft),
        "stdev_ttft_ms": stdev(ttft),
        "p95_ttft_ms": percentile(ttft, 0.95),
        "median_total_ms": median(totals),
        "p95_total_ms": percentile(totals, 0.95),
        "median_retrieval_ms": median(retrieval),
        "median_retrieval_lead_ms": median(lead),
        "median_positive_retrieval_lead_ms": median(positive_lead),
        "median_candidate_retrieval_lead_ms": median(candidate_lead),
        "p95_candidate_retrieval_lead_ms": percentile(candidate_lead, 0.95),
        "speculative_reuse_rate": (
            sum(bool(item.get("speculative_reuse")) for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
        "inflight_postcommit_overlap_rate": (
            sum(bool(item.get("inflight_postcommit_overlap")) for item in diagnostics)
            / len(diagnostics)
            if diagnostics
            else None
        ),
        "commit_fallback_rate": (
            sum(bool(item.get("commit_fallback")) for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
        "known_cache_hit_rate": (
            sum(item.get("cache_status") in {"hit", "mixed"} for item in cache_known)
            / len(cache_known)
            if cache_known
            else None
        ),
        "mean_input_tokens": mean([float(item["usage"]["input_tokens"]) for item in completed]),
        "mean_output_tokens": mean([float(item["usage"]["output_tokens"]) for item in completed]),
        "mean_usage_accounted_model_calls": mean(calls),
        "usage_accounted_model_calls": sum(calls),
        "mean_controller_calls": mean([float(value) for value in controller_calls]),
        "total_controller_calls": sum(controller_calls),
        "mean_retrieval_calls": mean([float(value) for value in retrieval_calls]),
        "total_retrieval_calls": sum(retrieval_calls),
        "mean_dynamic_tool_calls": mean([float(value) for value in dynamic_tool_calls]),
        "total_dynamic_tool_calls": sum(dynamic_tool_calls),
        "completed_dynamic_tool_calls": sum(completed_dynamic_tool_calls),
        "dynamic_tool_call_rate": (
            sum(value > 0 for value in dynamic_tool_calls) / len(dynamic_tool_calls)
            if dynamic_tool_calls
            else None
        ),
        "priced_outputs": len(costs),
        "cost_coverage": len(costs) / len(items) if items else None,
        "accounting_complete_outputs": len(accounting_complete),
        "accounting_complete_rate": (
            len(accounting_complete) / len(completed) if completed else None
        ),
        "fully_priced_output_rate": (len(accounting_complete) / len(items) if items else None),
        "unpriced_cancelled_controller_calls": unpriced_controller_calls,
        "unpriced_cancelled_retrieval_calls": unpriced_retrieval_calls,
        "unpriced_controller_timeout_calls": unpriced_controller_timeouts,
        "unpriced_controller_failure_calls": unpriced_controller_failures,
        "unpriced_retrieval_timeout_calls": unpriced_retrieval_timeouts,
        "unpriced_retrieval_failure_calls": unpriced_retrieval_failures,
        "unpriced_local_tool_calls": unpriced_local_tool_calls,
        "cost_metric_status": (
            "complete"
            if len(costs) == len(items) == len(accounting_complete)
            else "lower_bound_non_final"
        ),
        "observed_cost_usd": sum(costs),
        "minimum_mean_cost_per_output_usd": sum(costs) / len(items) if items else None,
        "mean_cost_usd": mean(costs),
        "post_commit_throughput_queries_per_minute": (
            len(completed) * 60_000 / sum(post_commit_wall)
            if completed and sum(post_commit_wall) > 0
            else None
        ),
    }


def paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[(row["id"], int(row["repetition"]))][row["path"]] = row
    complete_pairs = [
        pair
        for pair in grouped.values()
        if {"naive", "stream"} <= pair.keys()
        and "error" not in pair["naive"]
        and "error" not in pair["stream"]
    ]

    def delta(getter: Callable[[dict[str, Any]], float]) -> list[float]:
        return [getter(pair["stream"]) - getter(pair["naive"]) for pair in complete_pairs]

    ttft_delta = delta(lambda item: float(item["timing"]["submit_to_first_token_ms"]))
    total_delta = delta(lambda item: float(item["timing"]["total_response_ms"]))
    accounting_complete_pairs = [
        pair
        for pair in complete_pairs
        if pair["naive"]["estimated_cost_usd"].get("accounting_complete") is True
        and pair["stream"]["estimated_cost_usd"].get("accounting_complete") is True
    ]
    cost_delta = [
        float(pair["stream"]["estimated_cost_usd"]["total"])
        - float(pair["naive"]["estimated_cost_usd"]["total"])
        for pair in accounting_complete_pairs
    ]
    call_delta = delta(lambda item: float(item["usage"]["calls"]))
    relative_ttft = [
        (
            float(pair["stream"]["timing"]["submit_to_first_token_ms"])
            - float(pair["naive"]["timing"]["submit_to_first_token_ms"])
        )
        / float(pair["naive"]["timing"]["submit_to_first_token_ms"])
        for pair in complete_pairs
        if float(pair["naive"]["timing"]["submit_to_first_token_ms"]) > 0
    ]
    accuracy_delta = [
        int(pair["stream"]["expected_hit"]) - int(pair["naive"]["expected_hit"])
        for pair in complete_pairs
    ]
    return {
        "candidate_pairs": len(grouped),
        "completed_pairs": len(complete_pairs),
        "stream_ttft_win_rate": (
            sum(value < 0 for value in ttft_delta) / len(ttft_delta) if ttft_delta else None
        ),
        "median_stream_minus_naive_ttft_ms": median(ttft_delta),
        "p95_stream_minus_naive_ttft_ms": percentile(ttft_delta, 0.95),
        "median_stream_minus_naive_ttft_percent": (
            median(relative_ttft) * 100 if relative_ttft else None
        ),
        "median_stream_minus_naive_total_ms": median(total_delta),
        "mean_stream_minus_naive_cost_usd": mean(cost_delta),
        "cost_accounting_complete_pairs": len(accounting_complete_pairs),
        "cost_accounting_complete_pair_rate": (
            len(accounting_complete_pairs) / len(complete_pairs) if complete_pairs else None
        ),
        "mean_stream_minus_naive_usage_accounted_calls": mean(call_delta),
        "mean_stream_minus_naive_accuracy": mean(accuracy_delta),
        "accuracy_pairs": {
            "stream_only_correct": sum(value > 0 for value in accuracy_delta),
            "naive_only_correct": sum(value < 0 for value in accuracy_delta),
            "same_outcome": sum(value == 0 for value in accuracy_delta),
        },
    }


def grouped_path_summaries(
    rows: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], str],
    gold_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    result = {}
    for name, items in sorted(groups.items()):
        by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            by_path[item["path"]].append(item)
        result[name] = {
            path: path_summary(path_items, gold_by_id)
            for path, path_items in sorted(by_path.items())
        }
    return result


def grouped_paired_summaries(
    rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], str]
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {name: paired_summary(items) for name, items in sorted(groups.items())}


def summarize(
    rows: list[dict[str, Any]],
    gold_by_id: dict[str, dict[str, Any]],
    adjudications: dict[tuple[str, int, str], dict[str, Any]] | None = None,
    run_integrity: dict[str, Any] | None = None,
    adjudication_integrity: dict[str, Any] | None = None,
) -> dict:
    rows = annotate(rows, gold_by_id, adjudications)
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_path[row["path"]].append(row)
    stream_rows = [row for row in rows if row["path"] == "stream"]
    stream_stage_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stream_cache_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stream_rows:
        stream_stage_groups[row.get("diagnostics", {}).get("evidence_stage", "unknown")].append(row)
        stream_cache_groups[row.get("diagnostics", {}).get("cache_status", "unknown")].append(row)
    completed = [row for row in rows if "error" not in row]
    adjudicated = [row for row in completed if row["manual_adjudication"]]
    failures = len(rows) - len(completed)
    accounting_incomplete = sum(
        row.get("estimated_cost_usd", {}).get("accounting_complete") is not True
        for row in completed
    )
    run_integrity = run_integrity or {
        "status": "missing_or_incomplete",
        "issues": ["benchmark manifest was not validated"],
    }
    adjudication_integrity = adjudication_integrity or {
        "status": "not_requested",
        "path": None,
        "sha256": None,
        "issues": [],
    }
    manual_requested = adjudication_integrity.get("status") != "not_requested"
    manual_gate_complete = (
        manual_requested
        and bool(completed)
        and len(adjudicated) == len(completed)
        and adjudication_integrity.get("status") == "complete"
    )
    final_completeness = automatic_completeness(
        failures=failures,
        run_integrity=run_integrity,
    )
    return {
        "schema_version": 4,
        "scorer_sha256": sha256_file(Path(__file__).resolve()),
        "metric_notes": {
            "latency_delta_sign": "negative stream-minus-naive values favor StreamRAG",
            "accuracy": (
                "normalized expected-answer/alias containment with a conservative queried-"
                "relation contradiction guard; failures score incorrect; semantic correctness "
                "still requires prediction-bound manual adjudication"
            ),
            "citation_marker_rate": (
                "checks citation syntax only, not whether the source supports the claim"
            ),
            "supporting_doc_citation_rate": (
                "an exact cited chunk exists in the prediction's retrieved sources and resolves "
                "to a scorer-only gold supporting/acceptable document ID"
            ),
            "final_grounding_gate": (
                "automatic scoring is the required quick-benchmark gate; optional manual review "
                "uses perfect/acceptable/missing/incorrect adjudication"
            ),
            "cost": (
                "mean_cost_usd covers completed outputs only. Failed outputs and cancelled "
                "unpriced calls have unknown provider usage; observed totals and "
                "minimum_mean_cost_per_output_usd are non-final lower bounds when "
                "cost_metric_status is lower_bound_non_final"
            ),
            "usage_accounted_model_calls": (
                "counts calls represented in returned provider usage; controller attempts "
                "without returned usage are reported separately and are not silently counted"
            ),
            "throughput": "uses post-commit wall time and excludes simulated typing sleeps",
            "candidate_retrieval_lead": (
                "accepted_candidate_retrieval_lead_ms measures how much retrieval work for "
                "the ultimately accepted candidate completed before Send. It is retrieval "
                "headroom, not accepted/safe evidence lead and not measured TTFT saved"
            ),
            "stabilization_class": (
                "candidate metadata is heuristic/manual-review, assigned without path outputs"
            ),
        },
        "paths": {path: path_summary(items, gold_by_id) for path, items in sorted(by_path.items())},
        "manual_grounding_gate": {
            "status": (
                "complete"
                if manual_gate_complete
                else "missing_or_incomplete"
                if manual_requested
                else "not_requested"
            ),
            "required_for_automatic_benchmark": False,
            "completed_outputs": len(completed),
            "adjudicated_outputs": len(adjudicated),
            "labels": sorted(ADJUDICATION_LABELS),
        },
        "run_integrity_gate": run_integrity,
        "adjudication_integrity_gate": adjudication_integrity,
        "final_completeness_gate": {
            "status": "complete" if final_completeness else "missing_or_incomplete",
            "outputs": len(rows),
            "completed_outputs": len(completed),
            "failures": failures,
            "accounting_incomplete_outputs": accounting_incomplete,
            "adjudicated_outputs": len(adjudicated),
            "requirement": (
                "validated gold-blind run and offline evaluation freeze with zero failures; "
                "provider-cost accounting and manual adjudication are independent gates"
            ),
        },
        "cost_accounting_gate": {
            "status": "complete" if accounting_incomplete == 0 else "lower_bound_non_final",
            "completed_outputs": len(completed),
            "accounting_incomplete_outputs": accounting_incomplete,
            "requirement": (
                "complete only when every completed output has provider usage for every "
                "attempt; otherwise reported cost is an observed lower bound"
            ),
        },
        "paired": paired_summary(rows),
        "strata": {
            "by_repetition": grouped_path_summaries(
                rows, lambda row: str(row["repetition"]), gold_by_id
            ),
            "by_path_order_position": grouped_path_summaries(
                rows, lambda row: str(row.get("path_order_position", "unknown")), gold_by_id
            ),
            "by_word_count_bucket": grouped_path_summaries(
                rows, lambda row: row["word_count_bucket"], gold_by_id
            ),
            "by_stabilization_class": grouped_path_summaries(
                rows,
                lambda row: str(row.get("stabilization_class", "unreviewed")),
                gold_by_id,
            ),
            "paired_by_stabilization_class": grouped_paired_summaries(
                rows, lambda row: str(row.get("stabilization_class", "unreviewed"))
            ),
            "stream_by_evidence_stage": {
                stage: path_summary(items, gold_by_id)
                for stage, items in sorted(stream_stage_groups.items())
            },
            "stream_by_cache_status": {
                status: path_summary(items, gold_by_id)
                for status, items in sorted(stream_cache_groups.items())
            },
        },
    }


def display(value: float | None, suffix: str = "", digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


def percent(value: float | None) -> float | None:
    return value * 100 if value is not None else None


def money(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"${value:.{digits}f}"


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Benchmark summary",
        "",
        "Generated from frozen predictions and scorer-only golds. Negative paired latency",
        "deltas favor StreamRAG. Automatic expected-answer/alias matching is a proxy, not a",
        "semantic correctness judgment. Support additionally requires a valid exact-chunk",
        "citation resolving to an acceptable gold document. Manual adjudication is optional.",
        "",
        "| Path | Completed | Failures | Automatic match proxy | Support+valid citation | "
        "Manual semantic P/A | "
        "Median TTFT | p95 TTFT | Median total | Pre-Send reuse | In-flight overlap | "
        "Fallback | Usage-accounted calls/output | Cost/completed | Cost coverage | "
        "Accounting complete |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for path, values in summary["paths"].items():
        accuracy = display(percent(values["expected_answer_accuracy"]), "%", 1)
        supported_correct = display(percent(values["supported_expected_answer_rate"]), "%", 1)
        manual_correct = display(
            percent(values["manual_adjudication"]["perfect_or_acceptable_rate"]), "%", 1
        )
        reuse = display(percent(values["speculative_reuse_rate"]), "%", 1)
        inflight = display(percent(values["inflight_postcommit_overlap_rate"]), "%", 1)
        fallback = display(percent(values["commit_fallback_rate"]), "%", 1)
        cost_coverage = display(percent(values["cost_coverage"]), "%", 1)
        accounting_coverage = display(percent(values["accounting_complete_rate"]), "%", 1)
        if values["cost_metric_status"] != "complete":
            cost_coverage += " lower-bound/non-final"
        lines.append(
            f"| {path} | {values['completed']} | {values['failures']} | "
            f"{accuracy} | {supported_correct} | {manual_correct} | "
            f"{display(values['median_ttft_ms'], ' ms')} | "
            f"{display(values['p95_ttft_ms'], ' ms')} | "
            f"{display(values['median_total_ms'], ' ms')} | "
            f"{reuse} | {inflight} | {fallback} | "
            f"{display(values['mean_usage_accounted_model_calls'], '', 2)} | "
            f"{money(values['mean_cost_usd'])} | {cost_coverage} | {accounting_coverage} |"
        )
    gate = summary["manual_grounding_gate"]
    integrity_gate = summary["run_integrity_gate"]
    adjudication_gate = summary["adjudication_integrity_gate"]
    final_gate = summary["final_completeness_gate"]
    cost_gate = summary["cost_accounting_gate"]
    lines.extend(
        [
            "",
            f"Manual grounding gate: **{gate['status']}** "
            f"({gate['adjudicated_outputs']}/{gate['completed_outputs']} completed outputs).",
            f"Run integrity gate: **{integrity_gate['status']}** "
            f"({len(integrity_gate.get('issues', []))} issue(s)).",
            f"Adjudication integrity gate: **{adjudication_gate['status']}** "
            f"(SHA-256: {adjudication_gate.get('sha256') or 'missing'}).",
            f"Final completeness gate: **{final_gate['status']}** "
            f"({final_gate['failures']} failures; zero required).",
            f"Cost accounting gate: **{cost_gate['status']}** "
            f"({cost_gate['accounting_incomplete_outputs']} accounting-incomplete outputs).",
        ]
    )
    paired = summary["paired"]
    win_rate = display(percent(paired["stream_ttft_win_rate"]), "%", 1)
    paired_ttft = display(paired["median_stream_minus_naive_ttft_ms"], " ms")
    relative_ttft = display(paired["median_stream_minus_naive_ttft_percent"], "%", 1)
    paired_total = display(paired["median_stream_minus_naive_total_ms"], " ms")
    paired_cost = display(paired["mean_stream_minus_naive_cost_usd"], " USD", 6)
    lines.extend(
        [
            "",
            "## Paired StreamRAG deltas",
            "",
            f"- Completed A/B pairs: {paired['completed_pairs']} / {paired['candidate_pairs']}",
            f"- Stream TTFT win rate: {win_rate}",
            f"- Median Stream minus Naive TTFT: {paired_ttft}",
            f"- Median relative TTFT delta: {relative_ttft}",
            f"- Median Stream minus Naive total time: {paired_total}",
            f"- Mean Stream minus Naive cost (fully accounted pairs only): {paired_cost}",
            f"- Fully accounted cost pairs: {paired['cost_accounting_complete_pairs']} / "
            f"{paired['completed_pairs']}",
            f"- Automatic-proxy discordance: {paired['accuracy_pairs']}",
            "",
            "## Stream evidence stages",
            "",
            "Candidate retrieval lead is work moved before Send for the ultimately accepted",
            "candidate. It is not accepted/safe evidence lead and not measured TTFT saved.",
            "",
            "| Stage | Runs | Median TTFT | Median accepted-safe lead | "
            "Median / p95 candidate retrieval headroom | Automatic match proxy |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for stage, values in summary["strata"]["stream_by_evidence_stage"].items():
        stage_accuracy = display(percent(values["expected_answer_accuracy"]), "%", 1)
        lines.append(
            f"| {stage} | {values['outputs']} | {display(values['median_ttft_ms'], ' ms')} | "
            f"{display(values['median_retrieval_lead_ms'], ' ms')} | "
            f"{display(values['median_candidate_retrieval_lead_ms'], ' ms')} / "
            f"{display(values['p95_candidate_retrieval_lead_ms'], ' ms')} | "
            f"{stage_accuracy} |"
        )
    lines.extend(
        [
            "",
            "## Typed stabilization strata",
            "",
            "Candidate classes are heuristic/manual-review labels assigned without seeing path "
            "outputs; they are not measured stabilization points.",
            "",
            "| Class | Pairs | Naive TTFT | Stream TTFT | Stream reuse | "
            "Extra usage-accounted calls |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    path_strata = summary["strata"]["by_stabilization_class"]
    pair_strata = summary["strata"]["paired_by_stabilization_class"]
    for class_name, paths in path_strata.items():
        naive = paths.get("naive", {})
        stream = paths.get("stream", {})
        pairs = pair_strata[class_name]
        stream_reuse = display(percent(stream.get("speculative_reuse_rate")), "%", 1)
        extra_calls = display(pairs.get("mean_stream_minus_naive_usage_accounted_calls"), "", 2)
        lines.append(
            f"| {class_name} | {pairs['completed_pairs']} | "
            f"{display(naive.get('median_ttft_ms'), ' ms')} | "
            f"{display(stream.get('median_ttft_ms'), ' ms')} | {stream_reuse} | "
            f"{extra_calls} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score saved paired A/B predictions")
    parser.add_argument(
        "--predictions",
        type=Path,
        default=ROOT / "comparison" / "benchmark" / "results" / "predictions.jsonl",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        required=True,
        help="scorer-only gold, supplied explicitly to this offline process",
    )
    parser.add_argument(
        "--evaluation-manifest",
        type=Path,
        help="full frozen checksums.sha256; defaults to the gold file's directory",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="run manifest; defaults to predictions with .manifest.json suffix",
    )
    parser.add_argument(
        "--adjudications",
        type=Path,
        help=(
            "manual JSONL keyed by id/repetition/path with label, reviewer, and the exact "
            "prediction_sha256 returned by prediction_sha256(row)"
        ),
    )
    parser.add_argument(
        "--require-manual-adjudication",
        action="store_true",
        help=("add a strict manual-review extension to the automatic completeness gate"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "comparison" / "benchmark" / "results" / "summary.json",
    )
    args = parser.parse_args()
    manifest_path = args.manifest or args.predictions.with_suffix(".manifest.json")
    rows = read_jsonl(args.predictions)
    gold = read_jsonl(args.gold)
    adjudications = read_adjudications(args.adjudications) if args.adjudications else {}
    run_integrity = validate_run_integrity(
        rows,
        args.predictions,
        manifest_path,
        args.gold,
        args.evaluation_manifest,
    )
    adjudication_integrity = validate_adjudication_integrity(
        rows, adjudications, args.adjudications
    )
    summary = summarize(
        rows,
        {item["id"]: item for item in gold},
        adjudications,
        run_integrity,
        adjudication_integrity,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    args.output.with_suffix(".md").write_text(markdown(summary), encoding="utf-8")
    if args.require_manual_adjudication and (
        summary["final_completeness_gate"]["status"] != "complete"
        or summary["manual_grounding_gate"]["status"] != "complete"
        or summary["adjudication_integrity_gate"]["status"] != "complete"
    ):
        raise SystemExit(
            "manual-review extension gate failed: require an automatically complete run and "
            "exact content-addressed adjudication for every completed output"
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
