from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import Settings
from app.data.crag import (
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
    "trigger_reasoning_effort",
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
    "trigger_min_tokens",
    "trigger_min_new_tokens",
    "trigger_interval_ms",
    "trigger_max_presubmit_calls",
    "parallel_raw_retrieval",
    "settled_draft_delay_ms",
    "trigger_timeout_s",
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


def backend_source_sha256(root: Path) -> str:
    """Hash every executable backend source file and its dependency lock surface."""

    files = sorted((root / "app").rglob("*.py"))
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


def config_sha256(settings: Settings) -> str:
    """Hash every non-secret runtime setting that can affect benchmark behavior."""

    config = {name: getattr(settings, name) for name in CONFIG_FINGERPRINT_FIELDS}
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


def runtime_fingerprints(settings: Settings) -> dict[str, str]:
    """Return the startup identities for benchmark provenance."""
    snapshot = capture_dataset_snapshot(settings.dataset_dir)
    return {
        "backend_source_sha256": backend_source_sha256(settings.root),
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
