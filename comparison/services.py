#!/usr/bin/env python3
"""Prepare and run two isolated local API processes for A/B evaluation.

Final mode remains approval-gated and accepts only the redacted inference bundle.
An explicit development-candidate mode provisions the same isolated topology for
the dev-only smoke runner; it cannot make an artifact reportable or select queries.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from comparison.contracts import COMMON_IDENTITY_FIELDS, service_identity_issues
from comparison.prepare_inference_bundle import freeze_id, read_checksum_manifest, sha256_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "comparison" / "benchmark" / "results" / "inference_bundle"
DEFAULT_STATE_ROOT = ROOT / "comparison" / "benchmark" / "results" / "services"
IDENTITY_FIELDS = COMMON_IDENTITY_FIELDS


@dataclass(frozen=True)
class Instance:
    name: str
    port: int
    state_dir: Path

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def qdrant_path(self) -> Path:
        return self.state_dir / "qdrant"

    @property
    def runtime_db(self) -> Path:
        return self.state_dir / "runtime.sqlite3"

    @property
    def metrics_log(self) -> Path:
        return self.state_dir / "requests.jsonl"

    @property
    def service_log(self) -> Path:
        return self.state_dir / "service.log"


@dataclass
class ServiceProcess:
    instance: Instance
    process: subprocess.Popen[bytes]
    log_handle: Any


def dataset_status(dataset_dir: Path) -> str:
    summary_path = dataset_dir / "dataset_summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"dataset summary is missing: {summary_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"dataset summary is invalid JSON: {summary_path}") from exc
    return str(summary.get("selection", {}).get("approval_status", "unknown"))


def require_approved_dataset(dataset_dir: Path) -> dict[str, Any]:
    status = dataset_status(dataset_dir)
    if status != "approved_frozen":
        raise RuntimeError(
            f"dataset is {status!r}, not 'approved_frozen': {dataset_dir}. "
            "Final mode never sets ALLOW_UNREVIEWED_DATASET; use the explicit "
            "development-candidate targets only for dev smoke data."
        )
    forbidden_gold = [path.name for path in dataset_dir.iterdir() if "gold" in path.name.casefold()]
    if forbidden_gold:
        raise RuntimeError(f"inference bundle contains gold-named files: {forbidden_gold}")
    required = {
        "checksums.sha256",
        "dataset_summary.json",
        "test_queries.jsonl",
        "inference_bundle.json",
    }
    missing = sorted(name for name in required if not (dataset_dir / name).is_file())
    if missing:
        raise RuntimeError(f"inference bundle is missing files: {missing}")
    checksums = read_checksum_manifest(dataset_dir / "checksums.sha256")
    metadata = json.loads((dataset_dir / "inference_bundle.json").read_text(encoding="utf-8"))
    documents_name = str(metadata.get("documents_filename") or "documents.jsonl")
    if documents_name not in {"documents.jsonl", "documents.jsonl.bz2"}:
        raise RuntimeError("inference bundle has an invalid documents_filename")
    expected_entries = (required - {"checksums.sha256"}) | {documents_name}
    if set(checksums) != expected_entries:
        raise RuntimeError("inference checksum manifest has unexpected or missing entries")
    for name, expected in checksums.items():
        if sha256_file(dataset_dir / name) != expected:
            raise RuntimeError(f"inference bundle checksum mismatch: {name}")
    if metadata.get("bundle_role") != "inference_corpus":
        raise RuntimeError("dataset is not a redacted inference_corpus bundle")
    if metadata.get("approval_status") != "approved_frozen":
        raise RuntimeError("inference bundle metadata is not approved_frozen")
    evaluation_manifest = str(metadata.get("evaluation_manifest_sha256") or "")
    if metadata.get("freeze_id") != freeze_id(evaluation_manifest):
        raise RuntimeError("inference bundle freeze_id is invalid")
    if metadata.get("documents_sha256") != sha256_file(dataset_dir / documents_name):
        raise RuntimeError("inference bundle documents identity is invalid")
    if metadata.get("test_queries_sha256") != sha256_file(dataset_dir / "test_queries.jsonl"):
        raise RuntimeError("inference bundle query identity is invalid")
    return {
        **metadata,
        "serving_dataset_checksum": sha256_file(dataset_dir / "checksums.sha256"),
    }


def require_development_candidate(dataset_dir: Path) -> dict[str, Any]:
    """Validate the canonical candidate without reading or selecting test questions."""
    status = dataset_status(dataset_dir)
    if status != "candidate_pending_human_review":
        raise RuntimeError(
            "development mode requires 'candidate_pending_human_review', "
            f"got {status!r}: {dataset_dir}"
        )
    if (dataset_dir / "inference_bundle.json").exists():
        raise RuntimeError("development mode refuses an inference bundle")
    required = {
        "checksums.sha256",
        "dataset_summary.json",
        "dev_queries.jsonl",
        "selection_manifest.json",
    }
    missing = sorted(name for name in required if not (dataset_dir / name).is_file())
    if missing:
        raise RuntimeError(f"development dataset is missing files: {missing}")
    documents = next(
        (
            dataset_dir / name
            for name in ("documents.jsonl.bz2", "documents.jsonl")
            if (dataset_dir / name).is_file()
        ),
        None,
    )
    if documents is None:
        raise RuntimeError("development dataset is missing documents")
    checksums = read_checksum_manifest(dataset_dir / "checksums.sha256")
    required_entries = (required - {"checksums.sha256"}) | {documents.name}
    if not required_entries.issubset(checksums):
        raise RuntimeError("development checksum manifest is missing required entries")
    for name, expected in checksums.items():
        if Path(name).name != name:
            raise RuntimeError(f"development checksum entry is not a basename: {name}")
        path = dataset_dir / name
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"development dataset checksum mismatch: {name}")
    try:
        dev_rows = [
            json.loads(line)
            for line in (dataset_dir / "dev_queries.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
    except json.JSONDecodeError as exc:
        raise RuntimeError("development queries are invalid JSONL") from exc
    expected_count = (
        json.loads((dataset_dir / "dataset_summary.json").read_text(encoding="utf-8"))
        .get("selection", {})
        .get("dev_questions")
    )
    if not dev_rows or len(dev_rows) != expected_count:
        raise RuntimeError("development query count does not match dataset summary")
    if any(not str(row.get("id") or "").startswith("crag-text-dev-") for row in dev_rows):
        raise RuntimeError("development query file contains a non-development ID")
    manifest_sha256 = sha256_file(dataset_dir / "checksums.sha256")
    return {
        "approval_status": status,
        "bundle_role": "development_candidate",
        "evaluation_manifest_sha256": manifest_sha256,
        "serving_dataset_checksum": manifest_sha256,
        "freeze_id": freeze_id(manifest_sha256),
        "documents_sha256": sha256_file(documents),
    }


def instances(args: argparse.Namespace) -> tuple[Instance, Instance]:
    state_root = args.state_root.resolve()
    naive = Instance("naive", args.naive_port, state_root / "naive")
    stream = Instance("stream", args.stream_port, state_root / "stream")
    if naive.port == stream.port:
        raise RuntimeError("Naive and Stream ports must differ")
    if naive.state_dir == stream.state_dir or naive.qdrant_path == stream.qdrant_path:
        raise RuntimeError("Naive and Stream state/Qdrant paths must differ")
    if state_root in {Path("/"), Path.home(), ROOT, args.dataset_dir.resolve()}:
        raise RuntimeError(f"unsafe benchmark state root: {state_root}")
    return naive, stream


def child_environment(
    dataset_dir: Path,
    instance: Instance,
    *,
    allow_unreviewed_dataset: bool = False,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ALLOW_UNREVIEWED_DATASET": "1" if allow_unreviewed_dataset else "0",
            "DATASET_DIR": str(dataset_dir),
            # Empty values override any .env managed-Qdrant settings and force separate
            # persisted local stores for the two benchmark processes.
            "QDRANT_URL": "",
            "QDRANT_API_KEY": "",
            "QDRANT_PATH": str(instance.qdrant_path),
            "RUNTIME_DB": str(instance.runtime_db),
            "METRICS_LOG": str(instance.metrics_log),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def start_service(
    dataset_dir: Path,
    instance: Instance,
    *,
    allow_unreviewed_dataset: bool,
) -> ServiceProcess:
    instance.state_dir.mkdir(parents=True, exist_ok=True)
    log_handle = instance.service_log.open("ab", buffering=0)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        f"{instance.name}.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(instance.port),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=child_environment(
                dataset_dir,
                instance,
                allow_unreviewed_dataset=allow_unreviewed_dataset,
            ),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        log_handle.close()
        raise
    return ServiceProcess(instance, process, log_handle)


def stop_service(service: ServiceProcess) -> None:
    process = service.process
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    service.log_handle.close()


def log_tail(path: Path, lines: int = 20) -> str:
    if not path.is_file():
        return "(no service log)"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def wait_for_status(service: ServiceProcess, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error = "service did not respond"
    while time.monotonic() < deadline:
        return_code = service.process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"{service.instance.name} service exited with {return_code}\n"
                f"{log_tail(service.instance.service_log)}"
            )
        try:
            response = httpx.get(f"{service.instance.base_url}/v1/data/status", timeout=2)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise RuntimeError(
        f"timed out waiting for {service.instance.name}: {last_error}\n"
        f"{log_tail(service.instance.service_log)}"
    )


def validate_status(
    status: dict[str, Any],
    name: str,
    *,
    require_index: bool,
    contract: dict[str, Any] | None = None,
) -> None:
    if name in {"naive", "stream"}:
        identity_issues = service_identity_issues(
            status,
            name,
            root=ROOT,
            require_complete=require_index,
        )
        if identity_issues:
            raise RuntimeError(f"{name} service identity is invalid: {identity_issues}")
    expected_approval = (
        str(contract["approval_status"]) if contract is not None else "approved_frozen"
    )
    if status.get("approval_status") != expected_approval:
        raise RuntimeError(f"{name} API does not expose {expected_approval} data")
    if require_index and int(status.get("indexed_chunks") or 0) <= 0:
        raise RuntimeError(f"{name} index is empty; run the sync command first")
    missing = [field for field in IDENTITY_FIELDS if status.get(field) is None]
    if require_index and missing:
        raise RuntimeError(f"{name} status is missing benchmark identities: {missing}")
    if require_index and status.get("dataset_checksums_valid") is not True:
        raise RuntimeError(f"{name} dataset checksum validation failed")
    if require_index and status.get("index_metadata_ready") is not True:
        raise RuntimeError(f"{name} index metadata is not finalized and ready")
    if require_index and status.get("index_matches_current_corpus") is not True:
        raise RuntimeError(f"{name} index does not match the current corpus/config")
    if require_index and int(status["indexed_chunks"]) != int(status["indexed_desired_chunks"]):
        raise RuntimeError(f"{name} indexed chunk count does not match desired chunks")
    if require_index and status["index_source_sha256"] != status["current_index_source_sha256"]:
        raise RuntimeError(f"{name} index source fingerprint is stale")
    if contract is not None:
        expected = {
            "dataset_checksum": contract["evaluation_manifest_sha256"],
            "serving_dataset_checksum": contract["serving_dataset_checksum"],
            "freeze_id": contract["freeze_id"],
            "documents_sha256": contract["documents_sha256"],
        }
        mismatches = [field for field, value in expected.items() if status.get(field) != value]
        if mismatches:
            raise RuntimeError(f"{name} does not serve the selected dataset: {mismatches}")


def compare_statuses(
    statuses: dict[str, dict[str, Any]],
    *,
    verify_local_sources: bool = True,
) -> None:
    for name in ("naive", "stream"):
        issues = service_identity_issues(
            statuses[name],
            name,
            root=ROOT if verify_local_sources else None,
        )
        if issues:
            raise RuntimeError(f"{name} service identity is invalid: {issues}")
    mismatches = [
        field
        for field in IDENTITY_FIELDS
        if statuses["naive"].get(field) != statuses["stream"].get(field)
    ]
    if mismatches:
        raise RuntimeError(f"Naive and Stream index/config identities differ: {mismatches}")
    naive_instance = str(statuses["naive"].get("instance_id") or "")
    stream_instance = str(statuses["stream"].get("instance_id") or "")
    if not naive_instance or not stream_instance or naive_instance == stream_instance:
        raise RuntimeError("services do not expose two distinct backend instance IDs")


def public_configuration(
    dataset_dir: Path,
    state_root: Path,
    pair: tuple[Instance, Instance],
    contract: dict[str, Any],
    *,
    development_candidate: bool,
) -> dict[str, Any]:
    return {
        "dataset_dir": str(dataset_dir),
        "approval_status": contract["approval_status"],
        "bundle_role": contract["bundle_role"],
        "freeze_id": contract["freeze_id"],
        "mode": "development_candidate" if development_candidate else "final_approved",
        "allow_unreviewed_dataset": development_candidate,
        "reportable": False if development_candidate else "determined_by_runner",
        "openai_api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "state_root": str(state_root),
        "instances": {
            instance.name: {
                "base_url": instance.base_url,
                "qdrant_path": str(instance.qdrant_path),
                "runtime_db": str(instance.runtime_db),
                "metrics_log": str(instance.metrics_log),
                "service_log": str(instance.service_log),
            }
            for instance in pair
        },
    }


def preflight(
    args: argparse.Namespace, *, require_api_key: bool
) -> tuple[tuple[Instance, Instance], dict[str, Any]]:
    dataset_dir = args.dataset_dir.resolve()
    contract = (
        require_development_candidate(dataset_dir)
        if args.development_candidate
        else require_approved_dataset(dataset_dir)
    )
    pair = instances(args)
    if require_api_key and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured in the environment or .env")
    print(
        json.dumps(
            public_configuration(
                dataset_dir,
                args.state_root.resolve(),
                pair,
                contract,
                development_candidate=args.development_candidate,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return pair, contract


def sync_instance(
    dataset_dir: Path,
    contract: dict[str, Any],
    instance: Instance,
    timeout_s: float,
    *,
    development_candidate: bool,
) -> dict[str, Any]:
    service = start_service(
        dataset_dir,
        instance,
        allow_unreviewed_dataset=development_candidate,
    )
    try:
        initial = wait_for_status(service, timeout_s)
        validate_status(initial, instance.name, require_index=False, contract=contract)
        response = httpx.post(
            f"{instance.base_url}/v1/data/sync",
            timeout=httpx.Timeout(30, read=None),
        )
        response.raise_for_status()
        try:
            sync_report = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{instance.name} sync returned invalid JSON") from exc
        if not isinstance(sync_report, dict):
            raise RuntimeError(f"{instance.name} sync returned a non-object report")
        status_response = httpx.get(f"{instance.base_url}/v1/data/status", timeout=10)
        status_response.raise_for_status()
        status = status_response.json()
        validate_status(status, instance.name, require_index=True, contract=contract)
        status["sync_report"] = sync_report
        return status
    finally:
        stop_service(service)


def clone_quiescent_index(seed: Instance, target: Instance) -> None:
    if seed.state_dir == target.state_dir or seed.state_dir.parent != target.state_dir.parent:
        raise RuntimeError("seed and target must be distinct siblings under one state root")
    if not seed.qdrant_path.is_dir() or not seed.runtime_db.is_file():
        raise RuntimeError("quiescent seed is missing Qdrant or index metadata")
    if any(Path(f"{seed.runtime_db}{suffix}").exists() for suffix in ("-wal", "-shm")):
        raise RuntimeError("seed SQLite state is not quiescent")

    target.state_dir.mkdir(parents=True, exist_ok=True)
    if target.qdrant_path.exists():
        shutil.rmtree(target.qdrant_path)
    for path in (
        target.runtime_db,
        Path(f"{target.runtime_db}-wal"),
        Path(f"{target.runtime_db}-shm"),
        target.metrics_log,
        target.service_log,
    ):
        if path.exists():
            path.unlink()
    shutil.copytree(seed.qdrant_path, target.qdrant_path)
    shutil.copy2(seed.runtime_db, target.runtime_db)

    with sqlite3.connect(f"file:{target.runtime_db}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"cloned SQLite index metadata failed integrity check: {result}")


def inspect_instance(
    dataset_dir: Path,
    contract: dict[str, Any],
    instance: Instance,
    timeout_s: float,
    *,
    development_candidate: bool,
) -> dict[str, Any]:
    service = start_service(
        dataset_dir,
        instance,
        allow_unreviewed_dataset=development_candidate,
    )
    try:
        status = wait_for_status(service, timeout_s)
        validate_status(status, instance.name, require_index=True, contract=contract)
        return status
    finally:
        stop_service(service)


def command_sync(args: argparse.Namespace) -> None:
    pair, contract = preflight(args, require_api_key=True)
    dataset_dir = args.dataset_dir.resolve()
    state_root = args.state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".index-seed-", dir=state_root) as seed_root:
        seed = Instance("naive", args.naive_port, Path(seed_root))
        seed_status = sync_instance(
            dataset_dir,
            contract,
            seed,
            args.startup_timeout_s,
            development_candidate=args.development_candidate,
        )
        for instance in pair:
            clone_quiescent_index(seed, instance)

    statuses = {
        instance.name: inspect_instance(
            dataset_dir,
            contract,
            instance,
            args.startup_timeout_s,
            development_candidate=args.development_candidate,
        )
        for instance in pair
    }
    for status in statuses.values():
        status["sync_report"] = seed_status["sync_report"]
        status["index_provisioning"] = "one_quiescent_seed_cloned_to_isolated_stores"
    compare_statuses(statuses)
    output = state_root / "sync-status.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(statuses, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    label = "development-candidate" if args.development_candidate else "approved"
    print(f"Built one {label} index and cloned it into two isolated stores. Status: {output}")


def command_serve(args: argparse.Namespace) -> None:
    pair, contract = preflight(args, require_api_key=True)
    dataset_dir = args.dataset_dir.resolve()
    services: list[ServiceProcess] = []
    try:
        for instance in pair:
            services.append(
                start_service(
                    dataset_dir,
                    instance,
                    allow_unreviewed_dataset=args.development_candidate,
                )
            )
        statuses = {
            service.instance.name: wait_for_status(service, args.startup_timeout_s)
            for service in services
        }
        for name, status in statuses.items():
            validate_status(status, name, require_index=True, contract=contract)
        compare_statuses(statuses)
        next_command = "make benchmark-smoke" if args.development_candidate else "make benchmark"
        print(
            "Two isolated services are ready. In another terminal run:\n"
            f"  {next_command}\n"
            "Press Ctrl-C here after the run finishes."
        )
        while True:
            for service in services:
                return_code = service.process.poll()
                if return_code is not None:
                    raise RuntimeError(
                        f"{service.instance.name} service exited with {return_code}\n"
                        f"{log_tail(service.instance.service_log)}"
                    )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for service in reversed(services):
            stop_service(service)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync and serve two isolated evaluation APIs",
        epilog=(
            "Final mode is approval-gated. --development-candidate explicitly allows "
            "candidate indexing for the non-reportable dev-only smoke runner. The service "
            "launcher itself never executes questions."
        ),
    )
    parser.add_argument(
        "command",
        choices=("check", "sync", "serve"),
        help="check configuration, sync both indexes, or serve both APIs",
    )
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument(
        "--development-candidate",
        action="store_true",
        help=(
            "accept only candidate_pending_human_review data and set the child API's "
            "development override; never valid for a final run"
        ),
    )
    parser.add_argument("--naive-port", type=int, default=8001)
    parser.add_argument("--stream-port", type=int, default=8002)
    parser.add_argument("--startup-timeout-s", type=float, default=60.0)
    return parser


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args()
    if not 1 <= args.naive_port <= 65535 or not 1 <= args.stream_port <= 65535:
        parser.error("ports must be between 1 and 65535")
    if args.startup_timeout_s <= 0:
        parser.error("--startup-timeout-s must be positive")
    try:
        if args.command == "check":
            preflight(args, require_api_key=False)
        elif args.command == "sync":
            command_sync(args)
        else:
            command_serve(args)
    except KeyboardInterrupt as exc:
        raise SystemExit(130) from exc
    except (RuntimeError, httpx.HTTPError, OSError) as exc:
        raise SystemExit(f"benchmark service error: {exc}") from exc


def raise_keyboard_interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    # Ensure Ctrl-C reaches this parent, which then terminates both children.
    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
    main()
