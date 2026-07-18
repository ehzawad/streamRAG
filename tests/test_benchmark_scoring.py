from __future__ import annotations

import copy
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
scorer = importlib.import_module("bench.score")


def test_expected_hit_rejects_entity_mentioned_inside_abstention() -> None:
    gold = {"answer": "the wolf of wall street", "alt_answers": []}
    answer = (
        "The evidence mentions The Wolf of Wall Street, but it is insufficient to verify "
        "which movie scored higher."
    )

    assert scorer.expected_hit(answer, gold) is False


def test_false_premise_accepts_clear_evidence_based_abstention() -> None:
    gold = {"answer": "invalid question", "alt_answers": []}

    assert scorer.expected_hit(
        "The corpus does not establish a marriage; I can't name a spouse.", gold
    )


def test_false_premise_accepts_a_direct_evidence_based_correction() -> None:
    gold = {"answer": "invalid question", "alt_answers": []}

    assert scorer.expected_hit(
        "No. Fearless was Swift's second album, not her debut. [doc::c0001]", gold
    )


def test_expected_hit_handles_negation_boundaries_and_unrelated_caveats() -> None:
    rejected = (
        ("Gold", "The source does not provide Gold's score."),
        ("Gold", "There is no score for Gold."),
        ("Gold", "The winner was not Gold."),
        ("Gold", "I cannot verify whether Gold won."),
        ("US", "This business grew."),
        ("Alice, Bob", "Alice was not selected; Bob was selected."),
    )
    accepted = (
        ("Gold", "Gold received the better rating. I cannot verify the audience score."),
        ("Gold", "The answer is Gold, not Other."),
        ("US", "The US won."),
    )

    assert all(
        not scorer.expected_hit(answer, {"answer": gold, "alt_answers": []})
        for gold, answer in rejected
    )
    assert all(
        scorer.expected_hit(answer, {"answer": gold, "alt_answers": []})
        for gold, answer in accepted
    )


def test_false_premise_rejects_negated_abstention_and_direct_guess() -> None:
    gold = {"answer": "invalid question", "alt_answers": []}
    accepted = (
        "The evidence does not establish she is married, so no spouse can be identified.",
        "I can't name a spouse.",
        "The premise is false.",
        "Invalid question.",
    )
    rejected = (
        "This is not an invalid question; the spouse is Jane Doe.",
        "There is no evidence problem; the spouse is Jane Doe.",
        "The spouse is Jane Doe.",
    )

    assert all(scorer.expected_hit(answer, gold) for answer in accepted)
    assert all(not scorer.expected_hit(answer, gold) for answer in rejected)


def test_path_summary_separates_false_premise_and_answerable_abstentions() -> None:
    base = {
        "repetition": 1,
        "path": "naive",
        "timing": {
            "submit_to_first_token_ms": 1.0,
            "total_response_ms": 2.0,
            "retrieval_ms": 0.5,
        },
        "usage": {"calls": 1, "input_tokens": 1, "output_tokens": 1},
        "estimated_cost_usd": {"total": 0.01, "accounting_complete": True},
        "controller": {"calls": 1},
        "retrieval": {"calls": 1},
        "tool_traces": [],
        "post_commit_wall_ms": 2.0,
        "diagnostics": {},
    }
    rows = [
        {**base, "id": "false", "answer": "The premise is false."},
        {**base, "id": "normal", "answer": "There is insufficient evidence to verify it."},
    ]
    gold = {
        "false": {
            "answer": "invalid question",
            "alt_answers": [],
            "question_type": "false_premise",
        },
        "normal": {"answer": "Gold", "alt_answers": [], "question_type": "simple"},
    }

    summary = scorer.path_summary(scorer.annotate(rows, gold), gold)

    assert summary["false_premise_rejection_rate"] == 1.0
    assert summary["answerable_abstention_rate"] == 1.0


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_checksums(path: Path, files: list[Path], extra: dict[str, str] | None = None) -> None:
    entries = {item.name: scorer.sha256_file(item) for item in files}
    entries.update(extra or {})
    path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items())),
        encoding="utf-8",
    )


def valid_scoring_fixture(tmp_path: Path) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    evaluation = tmp_path / "evaluation"
    inference = tmp_path / "inference"
    results = tmp_path / "results"
    evaluation.mkdir()
    inference.mkdir()
    results.mkdir()
    queries = [
        {
            "id": f"q-{number}",
            "query": f"Which text fact number {number} is frozen?",
            "query_time": "2026-01-01T00:00:00Z",
        }
        for number in range(1, 21)
    ]
    gold_rows = [
        {
            "id": query["id"],
            "answer": "Frozen fact",
            "alt_answers": [],
            "supporting_doc_ids": ["doc-1"],
        }
        for query in queries
    ]
    evaluation_queries = evaluation / "test_queries.jsonl"
    gold_path = evaluation / "test_gold.jsonl"
    write_jsonl(evaluation_queries, queries)
    write_jsonl(gold_path, gold_rows)
    evaluation_manifest = evaluation / "checksums.sha256"
    write_checksums(evaluation_manifest, [evaluation_queries, gold_path])
    evaluation_manifest_sha256 = scorer.sha256_file(evaluation_manifest)
    freeze_id = scorer.opaque_freeze_id(evaluation_manifest_sha256)

    inference_queries = inference / "test_queries.jsonl"
    inference_queries.write_bytes(evaluation_queries.read_bytes())
    summary = inference / "dataset_summary.json"
    documents = inference / "documents.jsonl"
    metadata = inference / "inference_bundle.json"
    summary.write_text(
        json.dumps({"selection": {"approval_status": "approved_frozen"}}) + "\n",
        encoding="utf-8",
    )
    documents.write_text('{"id":"doc-1","text":"Frozen fact"}\n', encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bundle_role": "inference_corpus",
                "approval_status": "approved_frozen",
                "evaluation_manifest_sha256": evaluation_manifest_sha256,
                "freeze_id": freeze_id,
                "documents_sha256": scorer.sha256_file(documents),
                "test_queries_sha256": scorer.sha256_file(inference_queries),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    serving_manifest = inference / "checksums.sha256"
    write_checksums(serving_manifest, [summary, documents, inference_queries, metadata])

    run_tag = "fixture-run"
    path_urls = {"naive": "http://127.0.0.1:8001", "stream": "http://127.0.0.1:8002"}
    selected_queries = queries[:10]
    rows = []
    for repetition in range(1, 2):
        for query in selected_queries:
            for path in ("naive", "stream"):
                schedules = [] if path == "naive" else [{"transport_status": "aborted_at_commit"}]
                rows.append(
                    {
                        **query,
                        "repetition": repetition,
                        "path": path,
                        "service_base_url": path_urls[path],
                        "session_scope": (f"bench-{run_tag}-{repetition}-{query['id']}-{path}"),
                        "typing": {
                            "planned_commit_offset_ms": 1000.0,
                            "actual_commit_offset_ms": 1005.0,
                            "commit_drift_ms": 5.0,
                            "max_allowed_drift_ms": 100.0,
                            "drift_within_tolerance": True,
                        },
                        "snapshot_schedule": schedules,
                        "snapshot_transport_errors": 0,
                        "turn_cleanup": {"turn_cleanup_status": "not_required"},
                    }
                )
    predictions = results / "predictions.jsonl"
    write_jsonl(predictions, rows)

    identity_fields = {
        "model",
        "embedding_model",
        "reasoning_effort",
        "trigger_reasoning_effort",
        "summary_reasoning_effort",
        "service_tier",
        "index_pipeline_version",
        "dataset_checksum",
        "serving_dataset_checksum",
        "freeze_id",
        "documents_sha256",
        "index_checksum",
    }
    common_status = {field: f"fixture-{field}" for field in identity_fields}
    common_status.update(
        {
            "approval_status": "approved_frozen",
            "dataset_checksum": evaluation_manifest_sha256,
            "serving_dataset_checksum": scorer.sha256_file(serving_manifest),
            "freeze_id": freeze_id,
            "documents_sha256": scorer.sha256_file(documents),
        }
    )
    manifest = {
        "schema_version": 4,
        "run_tag": run_tag,
        "queries": Path(
            os.path.relpath(inference_queries, start=results)
        ).as_posix(),
        "queries_sha256": scorer.sha256_file(inference_queries),
        "query_ids": [query["id"] for query in selected_queries],
        "query_count": 10,
        "query_selection": {
            "method": "ordered_prefix",
            "source_query_count": 20,
            "selected_query_count": 10,
        },
        "freeze_id": freeze_id,
        "evaluation_manifest_sha256": evaluation_manifest_sha256,
        "serving_checksum_manifest": Path(
            os.path.relpath(serving_manifest, start=results)
        ).as_posix(),
        "serving_checksum_manifest_sha256": scorer.sha256_file(serving_manifest),
        "benchmark_harness_sha256": {
            "run_benchmark.py": scorer.sha256_file(ROOT / "bench" / "run_benchmark.py"),
            "typed_trace.py": scorer.sha256_file(ROOT / "bench" / "typed_trace.py"),
        },
        "predictions": predictions.name,
        "predictions_sha256": scorer.sha256_file(predictions),
        "prediction_rows_expected": 20,
        "prediction_rows_observed": 20,
        "finalized": True,
        "run_status": "completed_reportable",
        "reportable": True,
        "warmup_repetitions": 0,
        "warmup_outputs_expected": 0,
        "warmup_outputs_completed": 0,
        "warmup_failures": 0,
        "repetitions": 1,
        "max_typing_drift_ms": 100.0,
        "smoke_non_reportable": False,
        "path_urls": path_urls,
        "distinct_backend_instances": True,
        "compared_status_fields": sorted(identity_fields),
        "data_status": {
            "naive": {**common_status, "instance_id": "naive"},
            "stream": {**common_status, "instance_id": "stream"},
        },
        "preregistered_protocol_gate": {
            "status": "complete",
            "required_query_count": 10,
            "required_total_path_runs": 20,
            "required_warmup_repetitions": 0,
            "required_measured_repetitions": 1,
        },
        "warmup_gate": {"status": "complete"},
        "timing_drift_gate": {"status": "complete", "observations": 20, "violations": 0},
        "snapshot_transport_gate": {"status": "complete", "errors": 0},
        "case_deadline_gate": {"status": "complete", "deadline_failures": 0},
        "turn_cleanup_gate": {"status": "complete", "cleanup_failures": 0},
    }
    manifest_path = results / "predictions.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "rows": rows,
        "predictions": predictions,
        "manifest": manifest_path,
        "manifest_data": manifest,
        "gold": gold_path,
        "evaluation_manifest": evaluation_manifest,
    }


def test_offline_scorer_binds_gold_through_full_freeze(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "complete", integrity["issues"]
    assert integrity["gold_sha256"] == scorer.sha256_file(fixture["gold"])
    assert integrity["evaluation_manifest_sha256"] == scorer.sha256_file(
        fixture["evaluation_manifest"]
    )


def test_schema_v4_run_artifacts_remain_valid_after_directory_move(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    fixture = valid_scoring_fixture(source_root)
    manifest = fixture["manifest_data"]
    manifest_path = fixture["manifest"]
    archived_queries = source_root / "results" / "predictions.queries.jsonl"
    archived_queries.write_bytes((source_root / "inference" / "test_queries.jsonl").read_bytes())
    archived_serving_manifest = source_root / "results" / "predictions.serving-checksums.sha256"
    archived_serving_manifest.write_bytes(
        (source_root / "inference" / "checksums.sha256").read_bytes()
    )
    manifest.update(
        {
            "schema_version": 4,
            "queries": Path(
                os.path.relpath(archived_queries, start=manifest_path.parent)
            ).as_posix(),
            "serving_checksum_manifest": Path(
                os.path.relpath(archived_serving_manifest, start=manifest_path.parent)
            ).as_posix(),
            "predictions": "predictions.jsonl",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    shutil.copytree(source_root / "results", relocated / "results")
    shutil.copytree(source_root / "evaluation", relocated / "evaluation")
    relocated_predictions = relocated / "results" / "predictions.jsonl"
    relocated_manifest = relocated / "results" / "predictions.manifest.json"
    relocated_gold = relocated / "evaluation" / "test_gold.jsonl"
    relocated_evaluation_manifest = relocated / "evaluation" / "checksums.sha256"
    rows = scorer.read_jsonl(relocated_predictions)

    integrity = scorer.validate_run_integrity(
        rows,
        relocated_predictions,
        relocated_manifest,
        relocated_gold,
        relocated_evaluation_manifest,
    )

    assert integrity["status"] == "complete", integrity["issues"]


def test_offline_scorer_rejects_v3_and_absolute_artifact_paths(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    manifest["schema_version"] = 3
    manifest["predictions"] = str(fixture["predictions"].resolve())
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert "benchmark manifest schema_version must be 4" in integrity["issues"]
    assert "schema v4 artifact paths must be manifest-relative" in integrity["issues"]


def test_offline_scorer_rejects_wrong_gold_or_full_manifest(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    fixture["gold"].write_text('{"id":"q-1","answer":"tampered"}\n', encoding="utf-8")
    wrong_gold = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )
    assert wrong_gold["status"] == "missing_or_incomplete"
    assert any("gold checksum" in issue for issue in wrong_gold["issues"])

    fixture = valid_scoring_fixture(tmp_path / "second")
    fixture["evaluation_manifest"].write_text(
        fixture["evaluation_manifest"].read_text() + f"{'0' * 64}  extra.json\n",
        encoding="utf-8",
    )
    wrong_manifest = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )
    assert wrong_manifest["status"] == "missing_or_incomplete"
    assert any("runner freeze binding" in issue for issue in wrong_manifest["issues"])


def test_offline_scorer_rejects_extra_checksummed_query_rows(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    query_path = (fixture["manifest"].parent / manifest["queries"]).resolve()
    with query_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "id": "q-extra",
                    "query": "This row was not in the run manifest",
                    "query_time": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )
    manifest["queries_sha256"] = scorer.sha256_file(query_path)
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("selection contract is invalid" in issue for issue in integrity["issues"])


def test_offline_scorer_requires_exactly_ten_selected_frozen_queries(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    manifest["query_ids"] = manifest["query_ids"][:9]
    manifest["query_count"] = 9
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("exactly 10 frozen query IDs" in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_query_selection_reordering(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    manifest["query_ids"][0], manifest["query_ids"][1] = (
        manifest["query_ids"][1],
        manifest["query_ids"][0],
    )
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("ordered first 10" in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_non_preregistered_repetition_schedule(
    tmp_path: Path,
) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    manifest["warmup_repetitions"] = 1
    manifest["repetitions"] = 2
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("no warm-up and one measured repetition" in issue for issue in integrity["issues"])


def test_automatic_completeness_does_not_require_manual_adjudication() -> None:
    assert scorer.automatic_completeness(
        failures=0,
        accounting_incomplete=0,
        run_integrity={"status": "complete"},
    )


def test_manual_adjudication_is_an_optional_extension(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)

    gate = scorer.validate_adjudication_integrity(fixture["rows"], {}, None)

    assert gate["status"] == "not_requested"
    assert gate["issues"] == []


def test_adjudication_gate_binds_path_hash_and_rejects_extra_keys(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    rows = fixture["rows"]
    adjudication_rows = [
        {
            "id": row["id"],
            "repetition": row["repetition"],
            "path": row["path"],
            "label": "perfect",
            "reviewer": "reviewer-1",
        }
        for row in rows
    ]
    adjudications_path = tmp_path / "adjudications.jsonl"
    write_jsonl(adjudications_path, adjudication_rows)
    adjudications = scorer.read_adjudications(adjudications_path)

    gate = scorer.validate_adjudication_integrity(rows, adjudications, adjudications_path)

    assert gate["status"] == "complete"
    assert gate["path"] == adjudications_path.name
    assert gate["sha256"] == scorer.sha256_file(adjudications_path)

    extra = copy.deepcopy(adjudications)
    extra[("extra", 1, "naive")] = {
        "id": "extra",
        "repetition": 1,
        "path": "naive",
        "label": "perfect",
        "reviewer": "reviewer-1",
    }
    rejected = scorer.validate_adjudication_integrity(rows, extra, adjudications_path)
    assert rejected["status"] == "missing_or_incomplete"
    assert rejected["extra_keys"] == 1
