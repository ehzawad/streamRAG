#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.config import settings
from app.data.crag import (
    chunk_documents,
    dataset_review_status,
    load_documents,
    read_jsonl,
    verify_dataset_checksums,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the included CRAG evaluation dataset")
    parser.add_argument("--dataset-dir", type=Path, default=settings.dataset_dir)
    args = parser.parse_args()
    started = time.perf_counter()
    summary = json.loads((args.dataset_dir / "dataset_summary.json").read_text())
    checksums = verify_dataset_checksums(args.dataset_dir)
    documents = load_documents(args.dataset_dir)
    chunks = chunk_documents(documents, settings.chunk_tokens, settings.chunk_overlap)
    dev = list(read_jsonl(args.dataset_dir / "dev_queries.jsonl"))
    test = list(read_jsonl(args.dataset_dir / "test_queries.jsonl"))
    gold = list(read_jsonl(args.dataset_dir / "test_gold.jsonl"))
    expected = summary["corpus"]
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
                "approval_status": dataset_review_status(args.dataset_dir),
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
