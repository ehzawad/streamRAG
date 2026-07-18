from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

METRICS_CONTRACT_VERSION = 1

# These fields must be identical for a fair A/B run. Implementation ownership,
# source hashes, feature support, and process identity are validated separately.
COMMON_IDENTITY_FIELDS = (
    "approval_status",
    "indexed_chunks",
    "indexed_desired_chunks",
    "index_version",
    "index_checksum",
    "index_metadata_ready",
    "dataset_checksums_valid",
    "index_source_sha256",
    "current_index_source_sha256",
    "index_matches_current_corpus",
    "shared_source_sha256",
    "config_hash",
    "configuration",
    "dataset_checksum",
    "serving_dataset_checksum",
    "freeze_id",
    "dataset_sha256",
    "documents_sha256",
    "model",
    "embedding_model",
    "reasoning_effort",
    "summary_reasoning_effort",
    "service_tier",
    "index_pipeline_version",
)

PATH_IDENTITY_FIELDS = (
    "implementation",
    "metrics_contract_version",
    "supports_snapshots",
    "backend_source_sha256",
    "implementation_source_sha256",
)
REQUIRED_STATUS_FIELDS = (*COMMON_IDENTITY_FIELDS, *PATH_IDENTITY_FIELDS, "instance_id")
STREAM_STATUS_FIELDS = (
    "trigger_reasoning_effort",
    "trigger_min_tokens",
    "trigger_min_new_tokens",
    "trigger_interval_ms",
    "trigger_max_presubmit_calls",
    "parallel_raw_retrieval",
    "settled_draft_delay_ms",
    "trigger_timeout_s",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_sha256(root: Path, directories: tuple[str, ...], *, locks: bool) -> str:
    files = sorted(
        path
        for directory in directories
        for path in (root / directory).rglob("*.py")
        if "tests" not in path.parts
    )
    if locks:
        files.extend(
            path
            for name in ("pyproject.toml", "uv.lock", ".python-version")
            if (path := root / name).is_file()
        )
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def shared_source_sha256(root: Path) -> str:
    return _source_sha256(root, ("shared",), locks=True)


def implementation_source_sha256(root: Path, implementation: str) -> str:
    _validate_implementation(implementation)
    return _source_sha256(root, (implementation,), locks=False)


def backend_source_sha256(root: Path, implementation: str) -> str:
    _validate_implementation(implementation)
    return _source_sha256(root, ("shared", implementation), locks=True)


def config_sha256(configuration: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def service_identity_issues(
    status: dict[str, Any],
    implementation: str,
    *,
    root: Path | None = None,
    require_complete: bool = True,
) -> list[str]:
    _validate_implementation(implementation)
    issues: list[str] = []
    if require_complete:
        missing = [field for field in REQUIRED_STATUS_FIELDS if status.get(field) is None]
        if implementation == "stream":
            missing.extend(field for field in STREAM_STATUS_FIELDS if status.get(field) is None)
        if missing:
            issues.append(f"missing required identities: {missing}")
    if status.get("implementation") != implementation:
        issues.append(
            f"advertises implementation {status.get('implementation')!r}, "
            f"expected {implementation!r}"
        )
    if status.get("metrics_contract_version") != METRICS_CONTRACT_VERSION:
        issues.append("uses an unsupported metrics contract")
    expected_snapshots = implementation == "stream"
    if status.get("supports_snapshots") is not expected_snapshots:
        issues.append(f"supports_snapshots must be {expected_snapshots}")
    stream_fields_present = [field for field in STREAM_STATUS_FIELDS if field in status]
    if implementation == "naive" and stream_fields_present:
        issues.append(f"Naive advertises Stream-only configuration: {stream_fields_present}")
    configuration = status.get("configuration")
    if not isinstance(configuration, dict):
        issues.append("configuration is not an object")
    elif status.get("config_hash") != config_sha256(configuration):
        issues.append("config_hash does not match the advertised configuration")
    if root is not None:
        expected_sources = {
            "shared_source_sha256": shared_source_sha256(root),
            "implementation_source_sha256": implementation_source_sha256(root, implementation),
            "backend_source_sha256": backend_source_sha256(root, implementation),
        }
        mismatches = [
            field for field, value in expected_sources.items() if status.get(field) != value
        ]
        if mismatches:
            issues.append(f"source does not match the current tree: {mismatches}")
    return issues


def _validate_implementation(implementation: str) -> None:
    if implementation not in {"naive", "stream"}:
        raise ValueError(f"unknown RAG implementation: {implementation}")
