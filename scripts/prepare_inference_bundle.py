#!/usr/bin/env python3
"""Build an approval-gated inference bundle that contains no scorer-only gold.

The source evaluation checksum manifest is treated as an opaque freeze identity.
This script verifies and copies only inference-visible files; it never opens or
hashes test_gold.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALUATION_DIR = ROOT / "data" / "crag_eval"
DEFAULT_OUTPUT_DIR = ROOT / "bench" / "results" / "inference_bundle"
BUNDLE_STATIC_FILES = ("dataset_summary.json", "test_queries.jsonl")
DOCUMENT_FILENAMES = ("documents.jsonl", "documents.jsonl.bz2")
FREEZE_DOMAIN = b"typed-streamrag-eval-freeze-v1\0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_checksum_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, name = line.split(maxsplit=1)
        except ValueError as exc:
            raise RuntimeError(f"invalid freeze manifest line {line_number}") from exc
        name = name.lstrip("*").strip()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise RuntimeError(f"invalid SHA-256 at freeze manifest line {line_number}")
        if name in entries:
            raise RuntimeError(f"duplicate freeze manifest entry: {name}")
        entries[name] = digest
    return entries


def freeze_id(evaluation_manifest_sha256: str) -> str:
    return hashlib.sha256(FREEZE_DOMAIN + evaluation_manifest_sha256.encode("ascii")).hexdigest()


def approval_status(evaluation_dir: Path) -> str:
    summary = json.loads((evaluation_dir / "dataset_summary.json").read_text(encoding="utf-8"))
    return str(summary.get("selection", {}).get("approval_status", "unknown"))


def prepare_bundle(evaluation_dir: Path, output_dir: Path) -> dict[str, str | int]:
    evaluation_dir = evaluation_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"output already exists; refusing to overwrite: {output_dir}")
    if approval_status(evaluation_dir) != "approved_frozen":
        raise RuntimeError(
            "evaluation dataset is not approved_frozen; inference bundle creation refused"
        )

    evaluation_manifest = evaluation_dir / "checksums.sha256"
    if not evaluation_manifest.is_file():
        raise RuntimeError(f"evaluation freeze manifest is missing: {evaluation_manifest}")
    evaluation_checksums = read_checksum_manifest(evaluation_manifest)
    if "test_gold.jsonl" not in evaluation_checksums:
        raise RuntimeError("evaluation freeze manifest has no scorer-only gold binding")
    document_names = sorted(set(evaluation_checksums) & set(DOCUMENT_FILENAMES))
    if len(document_names) != 1:
        raise RuntimeError("evaluation freeze must bind exactly one corpus representation")
    documents_name = document_names[0]
    bundle_files = (*BUNDLE_STATIC_FILES, documents_name)
    for name in bundle_files:
        source = evaluation_dir / name
        expected = evaluation_checksums.get(name)
        if source.is_symlink():
            raise RuntimeError(f"inference-visible file must not be a symlink: {name}")
        if expected is None or not source.is_file() or sha256_file(source) != expected:
            raise RuntimeError(f"inference-visible file is not frozen correctly: {name}")

    evaluation_manifest_sha256 = sha256_file(evaluation_manifest)
    metadata: dict[str, str | int] = {
        "schema_version": 1,
        "bundle_role": "inference_corpus",
        "approval_status": "approved_frozen",
        "evaluation_manifest_sha256": evaluation_manifest_sha256,
        "freeze_id": freeze_id(evaluation_manifest_sha256),
        "documents_filename": documents_name,
        "documents_sha256": evaluation_checksums[documents_name],
        "test_queries_sha256": evaluation_checksums["test_queries.jsonl"],
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        for name in bundle_files:
            shutil.copy2(evaluation_dir / name, stage / name)
        (stage / "inference_bundle.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksums = {
            name: sha256_file(stage / name)
            for name in (*bundle_files, "inference_bundle.json")
        }
        (stage / "checksums.sha256").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
            encoding="utf-8",
        )
        if any("gold" in path.name.casefold() for path in stage.iterdir()):
            raise RuntimeError("gold-named file unexpectedly entered inference bundle")
        stage.rename(output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a redacted, gold-free inference bundle from an approved freeze"
    )
    parser.add_argument("--evaluation-dir", type=Path, default=DEFAULT_EVALUATION_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    try:
        metadata = prepare_bundle(args.evaluation_dir, args.output_dir)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"inference bundle error: {exc}") from exc
    print(json.dumps({"output": str(args.output_dir.resolve()), **metadata}, indent=2))


if __name__ == "__main__":
    main()
