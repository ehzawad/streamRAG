from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shared.config import Settings
from shared.data.crag import (
    VerifiedDatasetSnapshot,
    capture_dataset_snapshot,
    resolve_documents_path,
    sha256_file,
)

DATASET_FINGERPRINT_FILES = (
    "checksums.sha256",
    "dataset_summary.json",
    "dev_queries.jsonl",
    "selection_manifest.json",
    "test_gold.jsonl",
    "test_queries.jsonl",
)
INFERENCE_FINGERPRINT_FILES = (
    "checksums.sha256",
    "dataset_summary.json",
    "inference_bundle.json",
    "test_queries.jsonl",
)
INDEX_PIPELINE_VERSION = "typed-crag-dedup-chunk-payload-v2"

CONFIG_FINGERPRINT_FIELDS = (
    "openai_model",
    "embedding_model",
    "reasoning_effort",
    "summary_reasoning_effort",
    "openai_service_tier",
    "qdrant_collection",
    "embedding_dimensions",
    "chunk_tokens",
    "chunk_overlap",
    "retrieve_candidates",
    "top_k",
    "context_token_budget",
    "history_token_budget",
    "history_keep_turns",
    "retrieval_timeout_s",
    "answer_timeout_s",
    "summary_timeout_s",
    "post_answer_persistence_timeout_s",
)


def _combined_snapshot_hash(
    snapshot: VerifiedDatasetSnapshot,
    names: tuple[str, ...],
) -> str:
    checksums = snapshot.checksums()
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        file_checksum = (
            snapshot.serving_dataset_checksum
            if name == "checksums.sha256"
            else checksums.get(name, "missing")
        )
        digest.update(file_checksum.encode("ascii"))
        digest.update(b"\n")
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
    """Hash only code and dependencies shared by both implementations."""

    return _source_sha256(root, ("shared",), locks=True)


def _validate_implementation_package(root: Path, implementation: str) -> None:
    identifier = implementation.replace("-", "").replace("_", "")
    if not identifier.isalnum() or not (root / implementation).is_dir():
        raise ValueError(f"implementation package not found: {implementation}")


def implementation_source_sha256(root: Path, implementation: str) -> str:
    """Hash one implementation without importing or reading its peer."""

    _validate_implementation_package(root, implementation)
    return _source_sha256(root, (implementation,), locks=False)


def backend_source_sha256(root: Path, implementation: str) -> str:
    """Hash the independently deployable shared-plus-implementation service."""

    _validate_implementation_package(root, implementation)
    return _source_sha256(root, ("shared", implementation), locks=True)


def config_payload(settings: Settings) -> dict[str, object]:
    """Expose common non-secret configuration bound into benchmark provenance."""

    return {name: getattr(settings, name) for name in CONFIG_FINGERPRINT_FIELDS}


def config_sha256(settings: Settings) -> str:
    """Hash every common non-secret setting that can affect benchmark behavior."""

    config = config_payload(settings)
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def dataset_fingerprints(
    settings: Settings,
    snapshot: VerifiedDatasetSnapshot | None = None,
) -> dict[str, str]:
    """Derive dataset identities from one checksum-verified byte snapshot."""
    snapshot = snapshot or capture_dataset_snapshot(settings.dataset_dir)
    if "inference_bundle.json" in snapshot.checksums():
        fingerprint_files = (*INFERENCE_FINGERPRINT_FILES, snapshot.documents_filename)
    else:
        fingerprint_files = (*DATASET_FINGERPRINT_FILES, snapshot.documents_filename)
    return {
        "dataset_checksum": snapshot.dataset_checksum,
        "serving_dataset_checksum": snapshot.serving_dataset_checksum,
        "freeze_id": snapshot.freeze_id,
        "dataset_sha256": _combined_snapshot_hash(
            snapshot,
            fingerprint_files,
        ),
        "documents_sha256": snapshot.documents_sha256,
    }


def runtime_fingerprints(settings: Settings, implementation: str) -> dict[str, str]:
    """Return the startup identities for benchmark provenance."""
    snapshot = capture_dataset_snapshot(settings.dataset_dir)
    return {
        "backend_source_sha256": backend_source_sha256(settings.root, implementation),
        "shared_source_sha256": shared_source_sha256(settings.root),
        "implementation_source_sha256": implementation_source_sha256(
            settings.root,
            implementation,
        ),
        "config_hash": config_sha256(settings),
        **dataset_fingerprints(settings, snapshot),
    }


def index_source_sha256(settings: Settings) -> str:
    """Bind an index sync to its corpus and every index-shaping setting."""
    return index_source_sha256_for_documents(
        settings,
        sha256_file(resolve_documents_path(settings.dataset_dir)),
    )


def index_source_sha256_for_documents(
    settings: Settings,
    documents_sha256: str,
) -> str:
    """Bind index settings to a caller-supplied, already captured corpus digest."""
    payload = {
        "documents_sha256": documents_sha256,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_tokens": settings.chunk_tokens,
        "chunk_overlap": settings.chunk_overlap,
        "qdrant_collection": settings.qdrant_collection,
        "index_pipeline_version": INDEX_PIPELINE_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
