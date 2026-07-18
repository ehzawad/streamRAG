from __future__ import annotations

import copy
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
scorer = importlib.import_module("comparison.benchmark.score")
provenance = importlib.import_module("comparison.contracts.provenance")


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


def test_expected_hit_rejects_the_opposite_queried_relation() -> None:
    gold = {"answer": "Studio A", "alt_answers": []}
    question = "Which film had the smaller opening weekend?"

    assert not scorer.expected_hit("Studio A had the larger opening.", gold, question)
    assert not scorer.expected_hit("Studio A did not have the smaller opening.", gold, question)
    assert not scorer.expected_hit(
        "Studio A had an opening score that was not smaller than Studio B.",
        gold,
        question,
    )
    assert scorer.expected_hit(
        "Studio A had the smaller opening than Studio B's larger opening.",
        gold,
        question,
    )
    assert scorer.expected_hit("Studio A.", gold, question)


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


def test_support_requires_an_exact_retrieved_chunk_citation() -> None:
    gold = {
        "answer": "Gold",
        "alt_answers": [],
        "supporting_doc_ids": ["doc-1"],
    }
    base = {
        "id": "q-1",
        "query": "What is the answer?",
        "repetition": 1,
        "path": "naive",
        "sources": [{"chunk_id": "doc-1::c0001"}],
    }

    hallucinated = scorer.annotate([{**base, "answer": "Gold [doc-1::c9999]."}], {"q-1": gold})[0]
    valid = scorer.annotate([{**base, "answer": "Gold [doc-1::c0001]."}], {"q-1": gold})[0]

    assert hallucinated["has_citation_marker"] is True
    assert hallucinated["has_valid_citation"] is False
    assert hallucinated["cites_supporting_doc"] is False
    assert hallucinated["supported_expected_hit"] is False
    assert valid["has_valid_citation"] is True
    assert valid["cites_supporting_doc"] is True
    assert valid["supported_expected_hit"] is True


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
                schedules = (
                    []
                    if path == "naive"
                    else [
                        {
                            "revision": 1,
                            "transport_status": "completed",
                            "planned_offset_ms": 1200.0,
                            "character_count": len(query["query"].strip()),
                            "is_final": True,
                        }
                    ]
                )
                rows.append(
                    {
                        **query,
                        "repetition": repetition,
                        "path": path,
                        "service_base_url": path_urls[path],
                        "session_scope": (f"bench-{run_tag}-{repetition}-{query['id']}-{path}"),
                        "typing": {
                            "words_per_minute": 70.0,
                            "snapshot_interval_ms": 400,
                            "settled_draft_delay_ms": 500,
                            "typing_duration_ms": 1000.0,
                            "post_typing_dwell_ms": 5000.0,
                            "simulated_duration_ms": 6000.0,
                            "planned_commit_offset_ms": 6000.0,
                            "actual_commit_offset_ms": 6005.0,
                            "commit_drift_ms": 5.0,
                            "max_allowed_drift_ms": 100.0,
                            "drift_within_tolerance": True,
                        },
                        "snapshot_schedule": schedules,
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

    identity_fields = set(provenance.COMMON_IDENTITY_FIELDS)
    common_status = {field: f"fixture-{field}" for field in identity_fields}
    configuration = {"fixture": "configuration"}
    common_status.update(
        {
            "approval_status": "approved_frozen",
            "shared_source_sha256": provenance.shared_source_sha256(ROOT),
            "configuration": configuration,
            "config_hash": provenance.config_sha256(configuration),
            "current_index_source_sha256": "fixture-index-source",
            "dataset_checksums_valid": True,
            "dataset_sha256": "fixture-dataset",
            "dataset_checksum": evaluation_manifest_sha256,
            "serving_dataset_checksum": scorer.sha256_file(serving_manifest),
            "freeze_id": freeze_id,
            "documents_sha256": scorer.sha256_file(documents),
            "index_matches_current_corpus": True,
            "index_metadata_ready": True,
            "index_source_sha256": "fixture-index-source",
            "index_version": 1,
            "indexed_chunks": 1,
            "indexed_desired_chunks": 1,
        }
    )
    manifest = {
        "schema_version": 4,
        "run_tag": run_tag,
        "queries": Path(os.path.relpath(inference_queries, start=results)).as_posix(),
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
            "run_benchmark.py": scorer.sha256_file(
                ROOT / "comparison" / "benchmark" / "run_benchmark.py"
            ),
            "typed_trace.py": scorer.sha256_file(
                ROOT / "comparison" / "benchmark" / "typed_trace.py"
            ),
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
        "words_per_minute": 70.0,
        "post_typing_dwell_ms": 5000.0,
        "settled_draft_delay_ms": 500,
        "case_deadline_s": 45.0,
        "max_typing_drift_ms": 100.0,
        "smoke_non_reportable": False,
        "path_urls": path_urls,
        "backend_instance_ids": {"naive": "naive", "stream": "stream"},
        "distinct_backend_instances": True,
        "compared_status_fields": sorted(identity_fields),
        "data_status": {
            "naive": {
                **common_status,
                "implementation": "naive",
                "metrics_contract_version": 1,
                "supports_snapshots": False,
                "backend_source_sha256": provenance.backend_source_sha256(ROOT, "naive"),
                "implementation_source_sha256": provenance.implementation_source_sha256(
                    ROOT, "naive"
                ),
                "instance_id": "naive",
            },
            "stream": {
                **common_status,
                "trigger_reasoning_effort": "low",
                "trigger_min_tokens": 5,
                "trigger_min_new_tokens": 3,
                "trigger_interval_ms": 500,
                "trigger_max_presubmit_calls": 4,
                "parallel_raw_retrieval": True,
                "settled_draft_delay_ms": 500,
                "trigger_timeout_s": 4.0,
                "implementation": "stream",
                "metrics_contract_version": 1,
                "supports_snapshots": True,
                "backend_source_sha256": provenance.backend_source_sha256(ROOT, "stream"),
                "implementation_source_sha256": provenance.implementation_source_sha256(
                    ROOT, "stream"
                ),
                "instance_id": "stream",
            },
        },
        "preregistered_protocol_gate": {
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


def test_offline_scorer_rejects_tampered_typing_dwell_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    rows = copy.deepcopy(fixture["rows"])
    rows[0]["typing"]["post_typing_dwell_ms"] = 0.0
    write_jsonl(fixture["predictions"], rows)
    manifest = fixture["manifest_data"]
    manifest["predictions_sha256"] = scorer.sha256_file(fixture["predictions"])
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        rows,
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("typing/dwell contract" in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_aborted_exact_dwell_snapshot_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    rows = copy.deepcopy(fixture["rows"])
    stream_row = next(row for row in rows if row["path"] == "stream")
    exact_snapshot = next(
        schedule for schedule in stream_row["snapshot_schedule"] if schedule["is_final"] is True
    )
    exact_snapshot["transport_status"] = "aborted_at_commit"
    write_jsonl(fixture["predictions"], rows)
    manifest = fixture["manifest_data"]
    manifest["predictions_sha256"] = scorer.sha256_file(fixture["predictions"])
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        rows,
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("path-specific snapshot scheduling" in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_full_draft_not_processed_during_dwell(
    tmp_path: Path,
) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    rows = copy.deepcopy(fixture["rows"])
    stream_row = next(row for row in rows if row["path"] == "stream")
    stream_row["trace_events"] = []
    stream_row["settled_final_snapshot_observed"] = False
    write_jsonl(fixture["predictions"], rows)
    manifest = fixture["manifest_data"]
    manifest["predictions_sha256"] = scorer.sha256_file(fixture["predictions"])
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        rows,
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("path-specific snapshot scheduling" in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_forged_distinct_backend_identity(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    manifest["backend_instance_ids"]["stream"] = "naive"
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("distinct backend instances" in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_shared_stale_backend_identities(tmp_path: Path) -> None:
    cases = (
        ("backend_source_sha256", "source does not match the current tree"),
        ("config_hash", "config_hash does not match the advertised configuration"),
    )
    for field, expected_issue in cases:
        fixture = valid_scoring_fixture(tmp_path / field)
        manifest = fixture["manifest_data"]
        for path in ("naive", "stream"):
            manifest["data_status"][path][field] = "0" * 64
        fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

        integrity = scorer.validate_run_integrity(
            fixture["rows"],
            fixture["predictions"],
            fixture["manifest"],
            fixture["gold"],
            fixture["evaluation_manifest"],
        )

        assert integrity["status"] == "missing_or_incomplete"
        assert any(expected_issue in issue for issue in integrity["issues"])


def test_offline_scorer_rejects_a_shared_stale_index_identity(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    manifest = fixture["manifest_data"]
    for path in ("naive", "stream"):
        manifest["data_status"][path]["index_source_sha256"] = "stale"
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    integrity = scorer.validate_run_integrity(
        fixture["rows"],
        fixture["predictions"],
        fixture["manifest"],
        fixture["gold"],
        fixture["evaluation_manifest"],
    )

    assert integrity["status"] == "missing_or_incomplete"
    assert any("complete current index" in issue for issue in integrity["issues"])


def test_automatic_completeness_does_not_require_manual_adjudication() -> None:
    assert scorer.automatic_completeness(
        failures=0,
        run_integrity={"status": "complete"},
    )


def test_automatic_completeness_does_not_require_complete_cost_telemetry() -> None:
    summary = scorer.summarize(
        [
            {
                "id": "q-1",
                "query": "Which value?",
                "repetition": 1,
                "path": "stream",
                "answer": "Gold",
                "sources": [],
                "timing": {
                    "submit_to_first_token_ms": 1.0,
                    "total_response_ms": 2.0,
                    "retrieval_ms": 0.5,
                },
                "usage": {"calls": 1, "input_tokens": 1, "output_tokens": 1},
                "estimated_cost_usd": {"total": 0.01, "accounting_complete": False},
                "controller": {"calls": 1},
                "retrieval": {"calls": 1},
                "diagnostics": {},
                "tool_traces": [],
                "post_commit_wall_ms": 2.0,
            }
        ],
        {"q-1": {"answer": "Gold", "alt_answers": []}},
        run_integrity={"status": "complete", "issues": []},
    )

    assert summary["final_completeness_gate"]["status"] == "complete"
    assert summary["cost_accounting_gate"]["status"] == "lower_bound_non_final"


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
            "prediction_sha256": scorer.prediction_sha256(row),
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
        "prediction_sha256": "0" * 64,
    }
    rejected = scorer.validate_adjudication_integrity(rows, extra, adjudications_path)
    assert rejected["status"] == "missing_or_incomplete"
    assert rejected["extra_keys"] == 1


def test_adjudication_gate_rejects_a_label_for_an_old_prediction(tmp_path: Path) -> None:
    fixture = valid_scoring_fixture(tmp_path)
    row = fixture["rows"][0]
    key = (row["id"], row["repetition"], row["path"])
    adjudications = {
        key: {
            "id": row["id"],
            "repetition": row["repetition"],
            "path": row["path"],
            "label": "perfect",
            "reviewer": "reviewer-1",
            "prediction_sha256": scorer.prediction_sha256(row),
        }
    }
    changed = [{**row, "answer": "A different output"}, *fixture["rows"][1:]]

    gate = scorer.validate_adjudication_integrity(
        changed,
        adjudications,
        tmp_path / "adjudications.jsonl",
    )
    annotated = scorer.annotate(changed[:1], {}, adjudications)

    assert gate["status"] == "missing_or_incomplete"
    assert gate["mismatched_prediction_hashes"] == 1
    assert annotated[0]["manual_adjudication"] is None
