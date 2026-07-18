from __future__ import annotations

import bz2
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
bundle_module = importlib.import_module("scripts.prepare_inference_bundle")
prepare_bundle = bundle_module.prepare_bundle
sha256_file = bundle_module.sha256_file


def evaluation_fixture(root: Path, status: str = "approved_frozen") -> Path:
    root.mkdir()
    (root / "dataset_summary.json").write_text(
        json.dumps({"selection": {"approval_status": status}}) + "\n",
        encoding="utf-8",
    )
    (root / "documents.jsonl").write_text('{"id":"doc-1","text":"evidence"}\n')
    (root / "test_queries.jsonl").write_text(
        '{"id":"q-1","query":"hard question","query_time":"2026-01-01T00:00:00Z"}\n'
    )
    visible = ("dataset_summary.json", "documents.jsonl", "test_queries.jsonl")
    checksums = {name: sha256_file(root / name) for name in visible}
    # Deliberately bind an absent scorer-only file. Bundle construction succeeds only
    # if it never opens, hashes, or copies test_gold.jsonl.
    checksums["test_gold.jsonl"] = hashlib.sha256(b"offline-only-gold").hexdigest()
    (root / "checksums.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
        encoding="utf-8",
    )
    return root


def test_prepare_bundle_never_reads_or_copies_gold(tmp_path: Path) -> None:
    evaluation = evaluation_fixture(tmp_path / "evaluation")
    output = tmp_path / "inference"

    metadata = prepare_bundle(evaluation, output)

    assert set(path.name for path in output.iterdir()) == {
        "checksums.sha256",
        "dataset_summary.json",
        "documents.jsonl",
        "inference_bundle.json",
        "test_queries.jsonl",
    }
    assert not any("gold" in path.name.casefold() for path in output.iterdir())
    assert metadata["evaluation_manifest_sha256"] == sha256_file(evaluation / "checksums.sha256")
    assert metadata["freeze_id"] == bundle_module.freeze_id(metadata["evaluation_manifest_sha256"])
    redacted_names = {
        line.split(maxsplit=1)[1].strip()
        for line in (output / "checksums.sha256").read_text().splitlines()
    }
    assert redacted_names == {
        "dataset_summary.json",
        "documents.jsonl",
        "inference_bundle.json",
        "test_queries.jsonl",
    }


def test_prepare_bundle_preserves_compressed_corpus(tmp_path: Path) -> None:
    evaluation = evaluation_fixture(tmp_path / "evaluation")
    documents = evaluation / "documents.jsonl"
    compressed = evaluation / "documents.jsonl.bz2"
    compressed.write_bytes(bz2.compress(documents.read_bytes()))
    documents.unlink()
    checksums = {
        path.name: sha256_file(path)
        for path in evaluation.iterdir()
        if path.name != "checksums.sha256"
    }
    checksums["test_gold.jsonl"] = hashlib.sha256(b"offline-only-gold").hexdigest()
    (evaluation / "checksums.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
        encoding="utf-8",
    )

    output = tmp_path / "inference"
    metadata = prepare_bundle(evaluation, output)

    assert metadata["documents_filename"] == "documents.jsonl.bz2"
    assert (output / "documents.jsonl.bz2").is_file()
    assert not (output / "documents.jsonl").exists()


def test_prepare_bundle_refuses_pending_or_overwrite(tmp_path: Path) -> None:
    pending = evaluation_fixture(tmp_path / "pending", "candidate_pending_human_review")
    with pytest.raises(RuntimeError, match="not approved_frozen"):
        prepare_bundle(pending, tmp_path / "pending-output")

    evaluation = evaluation_fixture(tmp_path / "evaluation")
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        prepare_bundle(evaluation, output)


def test_prepare_bundle_rejects_symlinked_visible_source(tmp_path: Path) -> None:
    evaluation = evaluation_fixture(tmp_path / "evaluation")
    target = evaluation / "documents-target.jsonl"
    target.write_bytes((evaluation / "documents.jsonl").read_bytes())
    (evaluation / "documents.jsonl").unlink()
    (evaluation / "documents.jsonl").symlink_to(target)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        prepare_bundle(evaluation, tmp_path / "inference")
