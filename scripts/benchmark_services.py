#!/usr/bin/env python3
"""Prepare and run two isolated local API processes for the final A/B benchmark.

This script never runs benchmark questions and never bypasses dataset approval. The
`sync` command indexes the same approved corpus into two separate local Qdrant paths;
the `serve` command keeps both isolated processes in the foreground until interrupted.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

try:
    from scripts.prepare_inference_bundle import freeze_id, read_checksum_manifest, sha256_file
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from prepare_inference_bundle import (  # type: ignore[no-redef]
        freeze_id,
        read_checksum_manifest,
        sha256_file,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "bench" / "results" / "inference_bundle"
DEFAULT_STATE_ROOT = ROOT / "bench" / "results" / "services"
IDENTITY_FIELDS = (
    "approval_status",
    "indexed_chunks",
    "indexed_desired_chunks",
    "index_version",
    "index_checksum",
    "dataset_checksums_valid",
    "index_source_sha256",
    "current_index_source_sha256",
    "index_matches_current_corpus",
    "backend_source_sha256",
    "config_hash",
    "dataset_checksum",
    "serving_dataset_checksum",
    "freeze_id",
    "dataset_sha256",
    "documents_sha256",
    "model",
    "embedding_model",
    "reasoning_effort",
    "trigger_reasoning_effort",
    "summary_reasoning_effort",
    "service_tier",
    "index_pipeline_version",
)


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
            "This launcher never sets ALLOW_UNREVIEWED_DATASET."
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


def child_environment(dataset_dir: Path, instance: Instance) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ALLOW_UNREVIEWED_DATASET": "0",
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


def start_service(dataset_dir: Path, instance: Instance) -> ServiceProcess:
    instance.state_dir.mkdir(parents=True, exist_ok=True)
    log_handle = instance.service_log.open("ab", buffering=0)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(instance.port),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=child_environment(dataset_dir, instance),
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
    bundle: dict[str, Any] | None = None,
) -> None:
    if status.get("approval_status") != "approved_frozen":
        raise RuntimeError(f"{name} API does not expose approved_frozen data")
    if require_index and int(status.get("indexed_chunks") or 0) <= 0:
        raise RuntimeError(f"{name} index is empty; run the sync command first")
    missing = [field for field in IDENTITY_FIELDS if status.get(field) is None]
    if require_index and missing:
        raise RuntimeError(f"{name} status is missing benchmark identities: {missing}")
    if require_index and status.get("dataset_checksums_valid") is not True:
        raise RuntimeError(f"{name} dataset checksum validation failed")
    if require_index and status.get("index_matches_current_corpus") is not True:
        raise RuntimeError(f"{name} index does not match the current corpus/config")
    if require_index and int(status["indexed_chunks"]) != int(status["indexed_desired_chunks"]):
        raise RuntimeError(f"{name} indexed chunk count does not match desired chunks")
    if require_index and status["index_source_sha256"] != status["current_index_source_sha256"]:
        raise RuntimeError(f"{name} index source fingerprint is stale")
    if bundle is not None:
        expected = {
            "dataset_checksum": bundle["evaluation_manifest_sha256"],
            "serving_dataset_checksum": bundle["serving_dataset_checksum"],
            "freeze_id": bundle["freeze_id"],
            "documents_sha256": bundle["documents_sha256"],
        }
        mismatches = [field for field, value in expected.items() if status.get(field) != value]
        if mismatches:
            raise RuntimeError(f"{name} does not serve the selected inference bundle: {mismatches}")


def compare_statuses(statuses: dict[str, dict[str, Any]]) -> None:
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
    bundle: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dataset_dir": str(dataset_dir),
        "approval_status": bundle["approval_status"],
        "bundle_role": bundle["bundle_role"],
        "freeze_id": bundle["freeze_id"],
        "allow_unreviewed_dataset": False,
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
    bundle = require_approved_dataset(dataset_dir)
    pair = instances(args)
    if require_api_key and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured in the environment or .env")
    print(
        json.dumps(
            public_configuration(
                dataset_dir,
                args.state_root.resolve(),
                pair,
                bundle,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return pair, bundle


def sync_instance(
    dataset_dir: Path,
    bundle: dict[str, Any],
    instance: Instance,
    timeout_s: float,
) -> dict[str, Any]:
    service = start_service(dataset_dir, instance)
    try:
        initial = wait_for_status(service, timeout_s)
        validate_status(initial, instance.name, require_index=False, bundle=bundle)
        response = httpx.post(
            f"{instance.base_url}/v1/data/sync",
            timeout=httpx.Timeout(30, read=None),
        )
        response.raise_for_status()
        status_response = httpx.get(f"{instance.base_url}/v1/data/status", timeout=10)
        status_response.raise_for_status()
        status = status_response.json()
        validate_status(status, instance.name, require_index=True, bundle=bundle)
        return status
    finally:
        stop_service(service)


def command_sync(args: argparse.Namespace) -> None:
    pair, bundle = preflight(args, require_api_key=True)
    dataset_dir = args.dataset_dir.resolve()
    statuses = {
        instance.name: sync_instance(dataset_dir, bundle, instance, args.startup_timeout_s)
        for instance in pair
    }
    # Instance IDs differ because sync is sequential, and all content/config/index
    # identities must still match.
    compare_statuses(statuses)
    output = args.state_root.resolve() / "sync-status.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(statuses, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Synced two isolated approved indexes. Status: {output}")


def command_serve(args: argparse.Namespace) -> None:
    pair, bundle = preflight(args, require_api_key=True)
    dataset_dir = args.dataset_dir.resolve()
    services: list[ServiceProcess] = []
    try:
        for instance in pair:
            services.append(start_service(dataset_dir, instance))
        statuses = {
            service.instance.name: wait_for_status(service, args.startup_timeout_s)
            for service in services
        }
        for name, status in statuses.items():
            validate_status(status, name, require_index=True, bundle=bundle)
        compare_statuses(statuses)
        print(
            "Two isolated services are ready. In another terminal run:\n"
            "  make benchmark NAIVE_BASE_URL=http://127.0.0.1:8001 "
            "STREAM_BASE_URL=http://127.0.0.1:8002\n"
            "Press Ctrl-C here after the benchmark finishes."
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
        description="Sync and serve two isolated, approval-gated benchmark APIs",
        epilog=(
            "check is read-only; sync makes real embedding calls but runs no test queries; "
            "serve stays in the foreground and never bypasses approval"
        ),
    )
    parser.add_argument(
        "command",
        choices=("check", "sync", "serve"),
        help="check configuration, sync both indexes, or serve both APIs",
    )
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
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
