#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from app.config import settings
from app.data.crag import (
    capture_dataset_snapshot,
    chunk_documents,
    load_snapshot_documents,
)

EXPECTED_TEXT_INPUT_CONTRACT = {
    "answer_before_send": False,
    "commit": "full text at a higher revision",
    "post_typing_dwell_ms": 5000,
    "settled_draft_delay_ms": 500,
    "snapshot_interval_ms": 400,
    "snapshots": (
        "changed-only cumulative dirty text; unchanged 400 ms ticks emit no snapshot; "
        "after 500 ms unchanged, the latest delivered draft starts exact speculative "
        "retrieval without generating an answer"
    ),
}


def verified_bytes(dataset_dir: Path, name: str, checksums: dict[str, str]) -> bytes:
    content = (dataset_dir / name).read_bytes()
    if hashlib.sha256(content).hexdigest() != checksums.get(name):
        raise SystemExit(f"dataset changed during verification: {name}")
    return content


def verified_jsonl(dataset_dir: Path, name: str, checksums: dict[str, str]) -> list[dict]:
    content = verified_bytes(dataset_dir, name, checksums)
    rows: list[dict] = []
    for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON at {name}:{line_number}") from exc
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the included CRAG evaluation dataset")
    parser.add_argument("--dataset-dir", type=Path, default=settings.dataset_dir)
    args = parser.parse_args()
    started = time.perf_counter()
    snapshot = capture_dataset_snapshot(args.dataset_dir)
    checksums = snapshot.checksums()
    summary = json.loads(verified_bytes(args.dataset_dir, "dataset_summary.json", checksums))
    documents = load_snapshot_documents(snapshot)
    chunks = chunk_documents(documents, settings.chunk_tokens, settings.chunk_overlap)
    dev = verified_jsonl(args.dataset_dir, "dev_queries.jsonl", checksums)
    test = verified_jsonl(args.dataset_dir, "test_queries.jsonl", checksums)
    gold = verified_jsonl(args.dataset_dir, "test_gold.jsonl", checksums)
    expected = summary["corpus"]
    if summary.get("selection", {}).get("text_input_contract") != EXPECTED_TEXT_INPUT_CONTRACT:
        raise SystemExit("dataset text-input contract is not the fixed 400 ms/5,000 ms protocol")
    if len(documents) != int(expected["documents"]):
        raise SystemExit("document count does not match dataset summary")
    if len(chunks) != int(expected["estimated_index_points"]):
        raise SystemExit("chunk count does not match dataset summary")
    if (len(dev), len(test), len(gold)) != (5, 10, 10):
        raise SystemExit("dataset split shape is not the assignment-sized 5/10/10 contract")
    print(
        json.dumps(
            {
                "status": "verified",
                "approval_status": snapshot.approval_status,
                "checksummed_files": len(checksums),
                "documents": len(documents),
                "qdrant_points": len(chunks),
                "dev_questions": len(dev),
                "test_questions": len(test),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
