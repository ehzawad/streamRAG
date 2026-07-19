from __future__ import annotations

import hashlib
import importlib
import io
import json
import sqlite3
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from comparison.contracts.provenance import config_sha256

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
benchmark_services = importlib.import_module("comparison.services")
IDENTITY_FIELDS = benchmark_services.IDENTITY_FIELDS
child_environment = benchmark_services.child_environment
clone_quiescent_index = benchmark_services.clone_quiescent_index
compare_statuses = benchmark_services.compare_statuses
Instance = benchmark_services.Instance
instances = benchmark_services.instances
require_approved_dataset = benchmark_services.require_approved_dataset
require_development_candidate = benchmark_services.require_development_candidate
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


def development_dataset(
    root: Path,
    *,
    status: str = "candidate_pending_human_review",
    query_ids: tuple[str, ...] = ("crag-text-dev-001", "crag-text-dev-002"),
) -> Path:
    root.mkdir()
    (root / "dataset_summary.json").write_text(
        json.dumps(
            {
                "selection": {
                    "approval_status": status,
                    "dev_questions": len(query_ids),
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "dev_queries.jsonl").write_text(
        "".join(
            json.dumps({"id": query_id, "query": "development only"}) + "\n"
            for query_id in query_ids
        ),
        encoding="utf-8",
    )
    (root / "documents.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "selection_manifest.json").write_text("{}\n", encoding="utf-8")
    # The canonical evaluation directory also contains unseen queries and gold.
    # Development service validation checks their hashes but never selects them.
    (root / "test_queries.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "test_gold.jsonl").write_text("{}\n", encoding="utf-8")
    checksums = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(root.iterdir())
    ]
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


def test_development_mode_accepts_only_checksummed_dev_candidate(tmp_path: Path) -> None:
    dataset_dir = development_dataset(tmp_path / "dataset")
    contract = require_development_candidate(dataset_dir)

    assert contract["approval_status"] == "candidate_pending_human_review"
    assert contract["bundle_role"] == "development_candidate"
    assert (
        contract["serving_dataset_checksum"]
        == hashlib.sha256((dataset_dir / "checksums.sha256").read_bytes()).hexdigest()
    )


def test_development_mode_refuses_approved_or_non_dev_inputs(tmp_path: Path) -> None:
    approved = development_dataset(tmp_path / "approved", status="approved_frozen")
    with pytest.raises(RuntimeError, match="requires 'candidate_pending_human_review'"):
        require_development_candidate(approved)

    wrong_split = development_dataset(
        tmp_path / "wrong-split",
        query_ids=("crag-text-test-001",),
    )
    with pytest.raises(RuntimeError, match="non-development ID"):
        require_development_candidate(wrong_split)


def test_development_mode_refuses_checksum_drift(tmp_path: Path) -> None:
    dataset_dir = development_dataset(tmp_path / "dataset")
    (dataset_dir / "documents.jsonl").write_text('{"changed": true}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        require_development_candidate(dataset_dir)


def test_development_status_must_match_candidate_contract(tmp_path: Path) -> None:
    contract = require_development_candidate(development_dataset(tmp_path / "dataset"))
    status = {
        "approval_status": contract["approval_status"],
        "dataset_checksum": contract["evaluation_manifest_sha256"],
        "serving_dataset_checksum": contract["serving_dataset_checksum"],
        "freeze_id": contract["freeze_id"],
        "documents_sha256": contract["documents_sha256"],
    }
    validate_status(status, "dev", require_index=False, contract=contract)

    status["approval_status"] = "approved_frozen"
    with pytest.raises(RuntimeError, match="candidate_pending_human_review"):
        validate_status(status, "dev", require_index=False, contract=contract)


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
    dev_env = child_environment(dataset_dir, naive, allow_unreviewed_dataset=True)
    assert dev_env["ALLOW_UNREVIEWED_DATASET"] == "1"


def test_same_port_is_rejected(tmp_path: Path) -> None:
    dataset_dir = approved_dataset(tmp_path / "dataset")
    args = service_args(tmp_path, dataset_dir)
    args.stream_port = args.naive_port
    with pytest.raises(RuntimeError, match="ports must differ"):
        instances(args)


def test_quiescent_seed_is_cloned_into_independent_stores(tmp_path: Path) -> None:
    seed = Instance("naive", 18001, tmp_path / "seed")
    target = Instance("stream", 18002, tmp_path / "stream")
    seed.qdrant_path.mkdir(parents=True)
    (seed.qdrant_path / "storage.bin").write_bytes(b"seed-index")
    with sqlite3.connect(seed.runtime_db) as connection:
        connection.execute("CREATE TABLE metadata (value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES ('ready')")

    target.qdrant_path.mkdir(parents=True)
    (target.qdrant_path / "stale.bin").write_bytes(b"stale")
    target.runtime_db.write_bytes(b"stale")
    clone_quiescent_index(seed, target)

    assert (target.qdrant_path / "storage.bin").read_bytes() == b"seed-index"
    assert not (target.qdrant_path / "stale.bin").exists()
    with sqlite3.connect(target.runtime_db) as connection:
        assert connection.execute("SELECT value FROM metadata").fetchone() == ("ready",)

    (target.qdrant_path / "storage.bin").write_bytes(b"target-only")
    assert (seed.qdrant_path / "storage.bin").read_bytes() == b"seed-index"


def test_status_comparison_requires_matching_identities_and_distinct_instances() -> None:
    common = {field: f"value-{field}" for field in IDENTITY_FIELDS}
    configuration = {"fixture": "configuration"}
    common.update(
        {
            "configuration": configuration,
            "config_hash": config_sha256(configuration),
        }
    )
    statuses = {
        "naive": {
            **common,
            "implementation": "naive",
            "metrics_contract_version": 1,
            "supports_snapshots": False,
            "backend_source_sha256": "naive-backend",
            "implementation_source_sha256": "naive-source",
            "instance_id": "naive-instance",
        },
        "stream": {
            **common,
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
            "backend_source_sha256": "stream-backend",
            "implementation_source_sha256": "stream-source",
            "instance_id": "stream-instance",
        },
    }
    compare_statuses(statuses, verify_local_sources=False)

    statuses["stream"]["dataset_sha256"] = "different"
    with pytest.raises(RuntimeError, match="identities differ"):
        compare_statuses(statuses, verify_local_sources=False)


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
            "index_metadata_ready",
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
            "index_metadata_ready": True,
            "index_matches_current_corpus": True,
            "index_source_sha256": "source",
            "current_index_source_sha256": "source",
        }
    )
    validate_status(status, "fixture", require_index=True)

    status["index_metadata_ready"] = False
    with pytest.raises(RuntimeError, match="not finalized"):
        validate_status(status, "fixture", require_index=True)

    status["index_metadata_ready"] = True
    status["indexed_desired_chunks"] = 11
    with pytest.raises(RuntimeError, match="desired chunks"):
        validate_status(status, "fixture", require_index=True)


def test_service_process_launch_readiness_and_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = Instance("naive", 18001, tmp_path / "naive")
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    launched: dict[str, object] = {}

    class Process:
        terminated = False
        killed = False
        wait_timeouts: list[float] = []

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, *, timeout: float) -> int:
            self.wait_timeouts.append(timeout)
            return 0

        def kill(self) -> None:
            self.killed = True

    process = Process()

    def popen(command, **kwargs):
        launched["command"] = command
        launched.update(kwargs)
        return process

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {"ok": True}

    status_request: dict[str, object] = {}

    def get(url: str, *, timeout: float) -> Response:
        status_request.update(url=url, timeout=timeout)
        return Response()

    monkeypatch.setattr(benchmark_services.subprocess, "Popen", popen)
    monkeypatch.setattr(benchmark_services.httpx, "get", get)

    service = benchmark_services.start_service(
        dataset_dir,
        instance,
        allow_unreviewed_dataset=True,
    )
    status = benchmark_services.wait_for_status(service, timeout_s=1)

    assert launched["command"] == [
        sys.executable,
        "-m",
        "uvicorn",
        "naive.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        "18001",
    ]
    assert launched["cwd"] == benchmark_services.ROOT
    environment = launched["env"]
    assert isinstance(environment, dict)
    assert environment["ALLOW_UNREVIEWED_DATASET"] == "1"
    assert environment["DATASET_DIR"] == str(dataset_dir)
    assert launched["stdout"] is service.log_handle
    assert launched["stderr"] == subprocess.STDOUT
    assert status_request == {"url": f"{instance.base_url}/v1/data/status", "timeout": 2}
    assert status == {"ok": True}

    benchmark_services.stop_service(service)

    assert process.terminated is True
    assert process.killed is False
    assert process.wait_timeouts == [10]
    assert service.log_handle.closed is True


def test_service_shutdown_kills_a_process_that_ignores_terminate(tmp_path: Path) -> None:
    class Process:
        killed = False
        wait_timeouts: list[float] = []

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, *, timeout: float) -> int:
            self.wait_timeouts.append(timeout)
            if timeout == 10:
                raise subprocess.TimeoutExpired("uvicorn", timeout)
            return 0

        def kill(self) -> None:
            self.killed = True

    process = Process()
    log_handle = io.BytesIO()
    service = benchmark_services.ServiceProcess(
        Instance("stream", 18002, tmp_path / "stream"),
        process,
        log_handle,
    )

    benchmark_services.stop_service(service)

    assert process.wait_timeouts == [10, 5]
    assert process.killed is True
    assert log_handle.closed is True
