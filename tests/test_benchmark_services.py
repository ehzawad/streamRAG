from __future__ import annotations

import hashlib
import importlib
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
benchmark_services = importlib.import_module("scripts.benchmark_services")
IDENTITY_FIELDS = benchmark_services.IDENTITY_FIELDS
child_environment = benchmark_services.child_environment
compare_statuses = benchmark_services.compare_statuses
instances = benchmark_services.instances
require_approved_dataset = benchmark_services.require_approved_dataset
validate_status = benchmark_services.validate_status
freeze_id = benchmark_services.freeze_id


def approved_dataset(root: Path, status: str = "approved_frozen") -> Path:
    root.mkdir()
    (root / "dataset_summary.json").write_text(
        json.dumps({"selection": {"approval_status": status}}),
        encoding="utf-8",
    )
    for name in ("documents.jsonl", "test_queries.jsonl"):
        (root / name).write_text("{}\n", encoding="utf-8")
    evaluation_manifest_sha256 = "a" * 64
    (root / "inference_bundle.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bundle_role": "inference_corpus",
                "approval_status": "approved_frozen",
                "evaluation_manifest_sha256": evaluation_manifest_sha256,
                "freeze_id": freeze_id(evaluation_manifest_sha256),
                "documents_sha256": hashlib.sha256(
                    (root / "documents.jsonl").read_bytes()
                ).hexdigest(),
                "test_queries_sha256": hashlib.sha256(
                    (root / "test_queries.jsonl").read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    checksums = []
    for path in sorted(root.iterdir()):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (root / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return root


def test_approved_bundle_is_gold_free_and_content_addressed(tmp_path: Path) -> None:
    dataset_dir = approved_dataset(tmp_path / "dataset")
    bundle = require_approved_dataset(dataset_dir)

    assert bundle["bundle_role"] == "inference_corpus"
    assert (
        bundle["serving_dataset_checksum"]
        == hashlib.sha256((dataset_dir / "checksums.sha256").read_bytes()).hexdigest()
    )
    assert not any("gold" in path.name.casefold() for path in dataset_dir.iterdir())


def test_bundle_with_gold_named_file_is_rejected(tmp_path: Path) -> None:
    dataset_dir = approved_dataset(tmp_path / "dataset")
    (dataset_dir / "test_gold.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="gold-named"):
        require_approved_dataset(dataset_dir)


def service_args(tmp_path: Path, dataset_dir: Path) -> Namespace:
    return Namespace(
        dataset_dir=dataset_dir,
        state_root=tmp_path / "service-state",
        naive_port=18001,
        stream_port=18002,
    )


def test_approval_gate_never_accepts_candidate_data(tmp_path: Path) -> None:
    dataset_dir = approved_dataset(tmp_path / "dataset", "candidate_pending_human_review")
    with pytest.raises(RuntimeError, match="not 'approved_frozen'"):
        require_approved_dataset(dataset_dir)


def test_instances_and_child_state_are_isolated(tmp_path: Path) -> None:
    dataset_dir = approved_dataset(tmp_path / "dataset")
    naive, stream = instances(service_args(tmp_path, dataset_dir))

    assert naive.port != stream.port
    assert naive.qdrant_path != stream.qdrant_path
    naive_env = child_environment(dataset_dir, naive)
    stream_env = child_environment(dataset_dir, stream)
    assert naive_env["ALLOW_UNREVIEWED_DATASET"] == "0"
    assert naive_env["QDRANT_URL"] == stream_env["QDRANT_URL"] == ""
    assert naive_env["QDRANT_PATH"] != stream_env["QDRANT_PATH"]
    assert naive_env["RUNTIME_DB"] != stream_env["RUNTIME_DB"]
    assert naive_env["METRICS_LOG"] != stream_env["METRICS_LOG"]


def test_same_port_is_rejected(tmp_path: Path) -> None:
    dataset_dir = approved_dataset(tmp_path / "dataset")
    args = service_args(tmp_path, dataset_dir)
    args.stream_port = args.naive_port
    with pytest.raises(RuntimeError, match="ports must differ"):
        instances(args)


def test_status_comparison_requires_matching_identities_and_distinct_instances() -> None:
    common = {field: f"value-{field}" for field in IDENTITY_FIELDS}
    statuses = {
        "naive": {**common, "instance_id": "naive-instance"},
        "stream": {**common, "instance_id": "stream-instance"},
    }
    compare_statuses(statuses)

    statuses["stream"]["dataset_sha256"] = "different"
    with pytest.raises(RuntimeError, match="identities differ"):
        compare_statuses(statuses)


def test_status_validation_rejects_partial_or_stale_index() -> None:
    status = {
        field: f"value-{field}"
        for field in IDENTITY_FIELDS
        if field
        not in {
            "approval_status",
            "indexed_chunks",
            "indexed_desired_chunks",
            "dataset_checksums_valid",
            "index_matches_current_corpus",
            "index_source_sha256",
            "current_index_source_sha256",
        }
    }
    status.update(
        {
            "approval_status": "approved_frozen",
            "indexed_chunks": 10,
            "indexed_desired_chunks": 10,
            "dataset_checksums_valid": True,
            "index_matches_current_corpus": True,
            "index_source_sha256": "source",
            "current_index_source_sha256": "source",
        }
    )
    validate_status(status, "fixture", require_index=True)

    status["indexed_desired_chunks"] = 11
    with pytest.raises(RuntimeError, match="desired chunks"):
        validate_status(status, "fixture", require_index=True)
