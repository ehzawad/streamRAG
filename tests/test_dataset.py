from __future__ import annotations

import bz2
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings
from app.data.crag import (
    FORBIDDEN_DOCUMENT_KEYS,
    dataset_review_status,
    load_documents,
    read_jsonl,
    require_dataset_approval,
    resolve_documents_path,
    sha256_file,
    verify_dataset_checksums,
)
from app.fingerprints import runtime_fingerprints
from scripts.prepare_inference_bundle import prepare_bundle


def test_candidate_is_explicitly_review_gated() -> None:
    assert dataset_review_status(settings.dataset_dir) == "candidate_pending_human_review"
    with pytest.raises(RuntimeError, match="not human-approved"):
        require_dataset_approval(settings.dataset_dir, allow_unreviewed=False)


def test_fixed_split_and_no_runtime_gold_leakage() -> None:
    dev = list(read_jsonl(settings.dataset_dir / "dev_queries.jsonl"))
    test = list(read_jsonl(settings.dataset_dir / "test_queries.jsonl"))
    gold = list(read_jsonl(settings.dataset_dir / "test_gold.jsonl"))
    documents = list(read_jsonl(resolve_documents_path(settings.dataset_dir)))
    summary = json.loads((settings.dataset_dir / "dataset_summary.json").read_text())
    assert (len(dev), len(test), len(gold)) == (5, 10, 10)
    assert len(documents) == summary["corpus"]["documents"]
    assert summary["corpus"]["full_documents_only"] is True
    assert summary["corpus"]["documents"] == 250
    assert summary["corpus"]["estimated_index_points"] <= 1_000
    assert not ({row["id"] for row in dev} & {row["id"] for row in test})
    assert all(not (FORBIDDEN_DOCUMENT_KEYS & row.keys()) for row in documents)
    assert all("answer" not in row and "alt_answers" not in row for row in test)
    document_ids = {row["doc_id"] for row in documents}
    assert all(set(row.get("supporting_doc_ids", ())) <= document_ids for row in dev)
    assert all(set(row.get("supporting_doc_ids", ())) <= document_ids for row in gold)
    finance_dev = next(row for row in dev if row["id"] == "crag-text-dev-001")
    assert finance_dev["answer"] == "more than one year"
    assert {"over one year", "longer than one year"} <= set(finance_dev["alt_answers"])
    movie_dev = next(row for row in dev if row["id"] == "crag-text-dev-002")
    assert movie_dev["query"] == "which dune movie has better music, 1984 or 2021?"
    assert movie_dev["answer"] == "Dune (2021)"
    assert movie_dev["supporting_doc_ids"] == ["crag-global-046532bde5668fd1929c2408"]
    false_premise_dev = next(row for row in dev if row["question_type"] == "false_premise")
    assert false_premise_dev["answer"] == "invalid question"
    assert false_premise_dev["evidence_covered"] is True
    assert false_premise_dev["supporting_doc_ids"] == [
        "crag-global-bd5b1cb86ab3f862b0ed8803"
    ]
    assert all(len(row["supporting_doc_ids"]) == 1 for row in [*dev, *gold])
    audit = json.loads((settings.dataset_dir / "leakage_audit.json").read_text())
    assert audit["status"] == "pass"


def test_document_content_hashes_are_valid() -> None:
    summary = json.loads((settings.dataset_dir / "dataset_summary.json").read_text())
    assert len(load_documents(settings.dataset_dir)) == summary["corpus"]["documents"]


def _write_frozen_fixture(root: Path) -> None:
    files = {
        "dataset_summary.json": json.dumps(
            {"selection": {"approval_status": "approved_frozen"}}
        ),
        "dev_queries.jsonl": "{}\n",
        "documents.jsonl": "{}\n",
        "leakage_audit.json": "{}\n",
        "selection_manifest.json": "{}\n",
        "test_gold.jsonl": "{}\n",
        "test_queries.jsonl": "{}\n",
    }
    root.mkdir()
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    manifest = "\n".join(
        f"{sha256_file(root / name)}  {name}" for name in sorted(files)
    )
    (root / "checksums.sha256").write_text(f"{manifest}\n", encoding="utf-8")


def test_frozen_approval_requires_matching_checksum_manifest(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    _write_frozen_fixture(dataset)
    assert len(verify_dataset_checksums(dataset)) == 7
    assert require_dataset_approval(dataset, allow_unreviewed=False) == "approved_frozen"

    (dataset / "test_gold.jsonl").write_text('{"edited": true}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum mismatch: test_gold.jsonl"):
        require_dataset_approval(dataset, allow_unreviewed=False)


def test_checksum_verifier_and_reader_accept_one_compressed_corpus(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    _write_frozen_fixture(dataset)
    documents = dataset / "documents.jsonl"
    compressed = dataset / "documents.jsonl.bz2"
    compressed.write_bytes(bz2.compress(documents.read_bytes()))
    documents.unlink()
    files = sorted(path for path in dataset.iterdir() if path.name != "checksums.sha256")
    (dataset / "checksums.sha256").write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )

    verified = verify_dataset_checksums(dataset)

    assert "documents.jsonl.bz2" in verified
    assert resolve_documents_path(dataset) == compressed
    assert list(read_jsonl(compressed)) == [{}]


def test_gold_free_inference_bundle_preserves_opaque_freeze_identity(tmp_path: Path) -> None:
    evaluation = tmp_path / "evaluation"
    bundle = tmp_path / "inference"
    _write_frozen_fixture(evaluation)

    metadata = prepare_bundle(evaluation, bundle)
    verified = verify_dataset_checksums(bundle)
    fingerprints = runtime_fingerprints(replace(settings, dataset_dir=bundle))

    assert "test_gold.jsonl" not in verified
    assert not (bundle / "test_gold.jsonl").exists()
    assert set(verified) == {
        "dataset_summary.json",
        "documents.jsonl",
        "inference_bundle.json",
        "test_queries.jsonl",
    }
    assert fingerprints["dataset_checksum"] == metadata["evaluation_manifest_sha256"]
    assert fingerprints["freeze_id"] == metadata["freeze_id"]
    assert fingerprints["serving_dataset_checksum"] != fingerprints["dataset_checksum"]
