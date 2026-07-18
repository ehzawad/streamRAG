#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json

from shared.config import settings
from shared.data.crag import (
    chunk_documents,
    deduplicate_documents,
    load_snapshot_documents,
    require_dataset_snapshot,
)
from shared.data.index_state import IndexStateRepository
from shared.data.vector_store import QdrantVectorStore
from shared.fingerprints import index_source_sha256_for_documents
from shared.rag.embeddings import OpenAIEmbedder


async def run() -> None:
    parser = argparse.ArgumentParser(description="Build the pinned CRAG dense index")
    parser.add_argument("--allow-unreviewed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 512:
        parser.error("--batch-size must be between 1 and 512")
    allow = settings.allow_unreviewed_dataset or args.allow_unreviewed
    snapshot = require_dataset_snapshot(settings.dataset_dir, allow)
    documents = deduplicate_documents(load_snapshot_documents(snapshot))
    chunks = chunk_documents(documents, settings.chunk_tokens, settings.chunk_overlap)
    embedder = OpenAIEmbedder(
        settings.embedding_model,
        timeout_s=settings.openai_embedding_timeout_s,
        max_retries=settings.openai_embedding_max_retries,
    )
    store = QdrantVectorStore(
        settings,
        embedder,
        IndexStateRepository(settings.runtime_db),
    )
    await store.setup()
    try:
        report = await store.sync(
            chunks,
            index_source=index_source_sha256_for_documents(
                settings,
                snapshot.documents_sha256,
            ),
            batch_size=args.batch_size,
        )
    finally:
        await store.close()
    result = {
        **report.__dict__,
        "dataset_approval_status": snapshot.approval_status,
        "unique_documents": len(documents),
        "estimated_embedding_cost_usd": round(
            report.embedding_tokens / 1_000_000 * settings.embedding_input_per_million, 6
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(run())
