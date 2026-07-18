from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

from bench import score_dev
from bench.typed_trace import typing_duration_ms


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")


def valid_dev_fixture(tmp_path: Path) -> dict[str, Any]:
    dataset = tmp_path / "dataset"
    results = tmp_path / "results"
    dataset.mkdir()
    results.mkdir()
    queries = [
        {
            "id": f"dev-{number}",
            "query": f"Which development fact is number {number}?",
            "query_time": "03/12/2024, 12:24:41 PT",
        }
        for number in range(1, 3)
    ]
    query_path = dataset / "dev_queries.jsonl"
    documents_path = dataset / "documents.jsonl.bz2"
    write_jsonl(query_path, queries)
    documents_path.write_bytes(b"fixture corpus")
    checksum_path = dataset / "checksums.sha256"
    checksum_path.write_text(
        f"{score_dev.sha256_file(query_path)}  {query_path.name}\n"
        f"{score_dev.sha256_file(documents_path)}  {documents_path.name}\n",
        encoding="utf-8",
    )
    checksum_sha256 = score_dev.sha256_file(checksum_path)

    run_tag = "dev-fixture"
    path_urls = {"naive": "http://127.0.0.1:8001", "stream": "http://127.0.0.1:8002"}
    rows: list[dict[str, Any]] = []
    for query in queries:
        typing_duration = typing_duration_ms(query["query"], 70.0)
        planned_commit = typing_duration + 5000.0
        final_snapshot_offset = math.ceil(typing_duration / 400.0) * 400.0
        for path in ("naive", "stream"):
            schedule = []
            if path == "stream":
                schedule = [
                    {
                        "revision": 1,
                        "character_count": len(query["query"]),
                        "is_final": True,
                        "planned_offset_ms": final_snapshot_offset,
                        "transport_status": "completed",
                    }
                ]
            rows.append(
                {
                    **query,
                    "repetition": 1,
                    "path": path,
                    "service_base_url": path_urls[path],
                    "session_scope": f"bench-{run_tag}-1-{query['id']}-{path}",
                    "typing": {
                        "words_per_minute": 70.0,
                        "snapshot_interval_ms": 400,
                        "settled_draft_delay_ms": 500,
                        "typing_duration_ms": typing_duration,
                        "post_typing_dwell_ms": 5000.0,
                        "simulated_duration_ms": planned_commit,
                        "planned_commit_offset_ms": planned_commit,
                        "actual_commit_offset_ms": planned_commit + 1.0,
                        "commit_drift_ms": 1.0,
                        "max_allowed_drift_ms": 100.0,
                        "drift_within_tolerance": True,
                    },
                    "snapshot_schedule": schedule,
                    "settled_final_snapshot_observed": path == "stream",
                    "trace_events": (
                        [
                            {
                                "type": "draft.settled",
                                "revision": 1,
                                "query": query["query"],
                                "state": "starting",
                                "benchmark_offset_from_commit_ms": -4000.0,
                            },
                            {
                                "type": "retrieval.started",
                                "revision": 1,
                                "query": query["query"],
                                "benchmark_offset_from_commit_ms": -4000.0,
                            },
                        ]
                        if path == "stream"
                        else []
                    ),
                    "snapshot_transport_errors": 0,
                    "turn_cleanup": {"turn_cleanup_status": "not_required"},
                }
            )
    predictions = results / "predictions.jsonl"
    write_jsonl(predictions, rows)

    common_status: dict[str, Any] = {
        "approval_status": "candidate_pending_human_review",
        "backend_source_sha256": score_dev.backend_source_sha256(score_dev.ROOT),
        "config_hash": score_dev.config_sha256(score_dev.settings),
        "current_index_source_sha256": "index-source",
        "dataset_checksum": checksum_sha256,
        "dataset_checksums_valid": True,
        "dataset_sha256": "dataset",
        "documents_sha256": score_dev.sha256_file(documents_path),
        "embedding_model": "text-embedding-3-large",
        "freeze_id": score_dev.opaque_freeze_id(checksum_sha256),
        "index_checksum": "index-checksum",
        "index_matches_current_corpus": True,
        "index_metadata_ready": True,
        "index_pipeline_version": "typed-crag-dedup-chunk-payload-v2",
        "index_source_sha256": "index-source",
        "index_version": 1,
        "indexed_chunks": 2,
        "indexed_desired_chunks": 2,
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "service_tier": "default",
        "serving_dataset_checksum": checksum_sha256,
        "summary_reasoning_effort": "low",
        "trigger_reasoning_effort": "low",
        "settled_draft_delay_ms": 500,
    }
    manifest = {
        "schema_version": 4,
        "run_tag": run_tag,
        "created_unix_s": 1.0,
        "completed_unix_s": 2.0,
        "queries": Path(os.path.relpath(query_path, start=results)).as_posix(),
        "queries_sha256": score_dev.sha256_file(query_path),
        "query_ids": [query["id"] for query in queries],
        "query_count": len(queries),
        "query_selection": {
            "method": "ordered_prefix",
            "source_query_count": len(queries),
            "selected_query_count": len(queries),
        },
        "serving_checksum_manifest": Path(os.path.relpath(checksum_path, start=results)).as_posix(),
        "serving_checksum_manifest_sha256": checksum_sha256,
        "benchmark_harness_sha256": {
            "run_benchmark.py": score_dev.sha256_file(
                score_dev.ROOT / "bench" / "run_benchmark.py"
            ),
            "typed_trace.py": score_dev.sha256_file(score_dev.ROOT / "bench" / "typed_trace.py"),
        },
        "predictions": predictions.name,
        "predictions_sha256": score_dev.sha256_file(predictions),
        "prediction_rows_expected": len(rows),
        "prediction_rows_observed": len(rows),
        "completed_outputs": len(rows),
        "failures": 0,
        "deadline_failures": 0,
        "finalized": True,
        "run_status": "completed_non_reportable",
        "reportable": False,
        "warmup_repetitions": 0,
        "warmup_outputs_expected": 0,
        "warmup_outputs_completed": 0,
        "warmup_failures": 0,
        "repetitions": 1,
        "words_per_minute": 70.0,
        "post_typing_dwell_ms": 5000.0,
        "settled_draft_delay_ms": 500,
        "max_typing_drift_ms": 100.0,
        "case_deadline_s": 45.0,
        "smoke_non_reportable": True,
        "path_urls": path_urls,
        "distinct_service_urls": True,
        "backend_instance_ids": {"naive": "naive-instance", "stream": "stream-instance"},
        "distinct_backend_instances": True,
        "compared_status_fields": sorted(score_dev.DEV_REQUIRED_IDENTITY_FIELDS),
        "data_status": {
            "naive": {**common_status, "instance_id": "naive-instance"},
            "stream": {**common_status, "instance_id": "stream-instance"},
        },
        "preregistered_protocol_gate": copy.deepcopy(score_dev.DEV_PROTOCOL_GATE),
        "warmup_gate": {
            "status": "complete",
            "outputs_expected": 0,
            "outputs_completed": 0,
            "failures": 0,
            "cleanup_failures": 0,
            "retained_in_predictions": False,
        },
        "timing_drift_gate": {
            "status": "complete",
            "observations": len(rows),
            "violations": 0,
            "max_allowed_abs_drift_ms": 100.0,
            "max_observed_abs_drift_ms": 1.0,
        },
        "snapshot_transport_gate": {
            "status": "complete",
            "errors": 0,
            "async_sends_do_not_delay_commit": True,
        },
        "case_deadline_gate": {
            "status": "complete",
            "deadline_failures": 0,
            "deadline_s": 45.0,
        },
        "turn_cleanup_gate": {
            "status": "complete",
            "cleanup_failures": 0,
            "failure_turns_require_bounded_delete": True,
        },
    }
    manifest_path = results / "predictions.manifest.json"
    write_manifest(manifest_path, manifest)
    return {
        "dev_rows": [dict(query, _source_path=str(query_path.resolve())) for query in queries],
        "rows": rows,
        "predictions": predictions,
        "manifest": manifest,
        "manifest_path": manifest_path,
    }


def validate(fixture: dict[str, Any]) -> dict[str, Any]:
    return score_dev.validate_dev_run(fixture["rows"], fixture["dev_rows"], fixture["predictions"])


def test_dev_scorer_accepts_a_fully_bound_run(tmp_path: Path) -> None:
    fixture = valid_dev_fixture(tmp_path)

    integrity = validate(fixture)

    assert integrity["status"] == "complete", integrity["issues"]


def test_dev_scorer_rejects_a_tampered_prediction_hash(tmp_path: Path) -> None:
    fixture = valid_dev_fixture(tmp_path)
    fixture["manifest"]["predictions_sha256"] = "0" * 64
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert "prediction checksum does not match the finalized manifest" in integrity["issues"]


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    (("query", "A different question"), ("query_time", "tampered timestamp")),
)
def test_dev_scorer_rejects_tampered_query_payload_even_with_a_new_hash(
    tmp_path: Path,
    field: str,
    tampered_value: str,
) -> None:
    fixture = valid_dev_fixture(tmp_path)
    fixture["rows"][0][field] = tampered_value
    write_jsonl(fixture["predictions"], fixture["rows"])
    fixture["manifest"]["predictions_sha256"] = score_dev.sha256_file(fixture["predictions"])
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert any("prediction query payload mismatch" in issue for issue in integrity["issues"])


def test_dev_scorer_rejects_tampered_harness_and_service_identity(tmp_path: Path) -> None:
    fixture = valid_dev_fixture(tmp_path)
    fixture["manifest"]["benchmark_harness_sha256"]["run_benchmark.py"] = "0" * 64
    fixture["manifest"]["data_status"]["stream"]["config_hash"] = "different"
    fixture["manifest"]["backend_instance_ids"]["stream"] = "naive-instance"
    fixture["manifest"]["data_status"]["stream"]["instance_id"] = "naive-instance"
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert any("benchmark harness source" in issue for issue in integrity["issues"])
    assert any("backend identity mismatch" in issue for issue in integrity["issues"])
    assert any("distinct backend instances" in issue for issue in integrity["issues"])


@pytest.mark.parametrize(
    ("field", "expected_issue"),
    (
        ("backend_source_sha256", "backend source does not match"),
        ("config_hash", "runtime configuration does not match"),
    ),
)
def test_dev_scorer_rejects_shared_stale_backend_identity(
    tmp_path: Path,
    field: str,
    expected_issue: str,
) -> None:
    fixture = valid_dev_fixture(tmp_path)
    for path in ("naive", "stream"):
        fixture["manifest"]["data_status"][path][field] = "0" * 64
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert any(expected_issue in issue for issue in integrity["issues"])


def test_dev_scorer_rejects_a_shared_stale_index_identity(tmp_path: Path) -> None:
    fixture = valid_dev_fixture(tmp_path)
    for path in ("naive", "stream"):
        fixture["manifest"]["data_status"][path]["index_source_sha256"] = "stale"
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert any("complete current index" in issue for issue in integrity["issues"])


def test_dev_scorer_rejects_aborted_exact_dwell_snapshot_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    fixture = valid_dev_fixture(tmp_path)
    stream_row = next(row for row in fixture["rows"] if row["path"] == "stream")
    stream_row["snapshot_schedule"][0]["transport_status"] = "aborted_at_commit"
    write_jsonl(fixture["predictions"], fixture["rows"])
    fixture["manifest"]["predictions_sha256"] = score_dev.sha256_file(fixture["predictions"])
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert any("path-specific snapshot scheduling" in issue for issue in integrity["issues"])


def test_dev_scorer_rejects_full_draft_not_processed_during_dwell(tmp_path: Path) -> None:
    fixture = valid_dev_fixture(tmp_path)
    stream_row = next(row for row in fixture["rows"] if row["path"] == "stream")
    stream_row["trace_events"] = []
    stream_row["settled_final_snapshot_observed"] = False
    write_jsonl(fixture["predictions"], fixture["rows"])
    fixture["manifest"]["predictions_sha256"] = score_dev.sha256_file(fixture["predictions"])
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert any("path-specific snapshot scheduling" in issue for issue in integrity["issues"])


@pytest.mark.parametrize(
    ("gate_name", "expected_issue"),
    (
        ("preregistered_protocol_gate", "development preregistered protocol gate is invalid"),
        ("warmup_gate", "development warmup gate is invalid"),
        ("timing_drift_gate", "development timing-drift gate is invalid"),
        ("snapshot_transport_gate", "development snapshot-transport gate is invalid"),
        ("case_deadline_gate", "development case-deadline gate is invalid"),
        ("turn_cleanup_gate", "development turn-cleanup gate is invalid"),
    ),
)
def test_dev_scorer_rejects_an_incomplete_manifest_gate(
    tmp_path: Path,
    gate_name: str,
    expected_issue: str,
) -> None:
    fixture = valid_dev_fixture(tmp_path)
    fixture["manifest"][gate_name]["status"] = "tampered"
    write_manifest(fixture["manifest_path"], fixture["manifest"])

    integrity = validate(fixture)

    assert integrity["status"] == "invalid"
    assert expected_issue in integrity["issues"]
