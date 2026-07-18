from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import Settings
from app.data.crag import resolve_documents_path, sha256_file

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
    "trigger_timeout_s",
    "retrieval_timeout_s",
    "answer_timeout_s",
    "summary_timeout_s",
    "post_answer_persistence_timeout_s",
)


def _combined_file_hash(root: Path, names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in names:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((sha256_file(path) if path.is_file() else "missing").encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _source_tree_hash(root: Path) -> str:
    files = sorted((root / "app").rglob("*.py"))
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def runtime_fingerprints(settings: Settings) -> dict[str, str]:
    """Return non-secret, content-addressed identities for benchmark provenance."""
    config = {name: getattr(settings, name) for name in CONFIG_FINGERPRINT_FIELDS}
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = settings.dataset_dir / "checksums.sha256"
    documents = resolve_documents_path(settings.dataset_dir)
    serving_dataset_checksum = sha256_file(manifest) if manifest.is_file() else "missing"
    inference_path = settings.dataset_dir / "inference_bundle.json"
    if inference_path.is_file():
        inference = json.loads(inference_path.read_text(encoding="utf-8"))
        dataset_checksum = str(inference.get("evaluation_manifest_sha256") or "invalid")
        freeze_id = str(inference.get("freeze_id") or "invalid")
        fingerprint_files = (*INFERENCE_FINGERPRINT_FILES, documents.name)
    else:
        dataset_checksum = serving_dataset_checksum
        freeze_id = hashlib.sha256(
            b"typed-streamrag-eval-freeze-v1\0" + dataset_checksum.encode("ascii")
        ).hexdigest()
        fingerprint_files = (*DATASET_FINGERPRINT_FILES, documents.name)
    return {
        "backend_source_sha256": _source_tree_hash(settings.root),
        "config_hash": config_hash,
        "dataset_checksum": dataset_checksum,
        "serving_dataset_checksum": serving_dataset_checksum,
        "freeze_id": freeze_id,
        "dataset_sha256": _combined_file_hash(
            settings.dataset_dir,
            fingerprint_files,
        ),
        "documents_sha256": sha256_file(documents),
    }


def index_source_sha256(settings: Settings) -> str:
    """Bind an index sync to its corpus and every index-shaping setting."""
    payload = {
        "documents_sha256": sha256_file(resolve_documents_path(settings.dataset_dir)),
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
