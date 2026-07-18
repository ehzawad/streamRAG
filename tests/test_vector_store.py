from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import numpy as np
import pytest

from app.config import settings
from app.data.index_state import IndexStateRepository
from app.data.vector_store import QdrantVectorStore
from app.models import Chunk


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self):
        self.embedded_texts = 0

    async def embed(self, texts):
        self.embedded_texts += len(texts)
        vectors = []
        for text in texts:
            vector = np.zeros(8, dtype=np.float32)
            vector[sum(text.encode("utf-8")) % 8] = 1.0
            vectors.append(vector)
        return np.stack(vectors), sum(len(text.split()) for text in texts)


class SlowFakeEmbedder(FakeEmbedder):
    async def embed(self, texts):
        await asyncio.sleep(0.02)
        return await super().embed(texts)


class CountingIndexStateRepository(IndexStateRepository):
    def __init__(self, path):
        super().__init__(path)
        self.version_reads = 0

    async def version(self, collection: str) -> int:
        self.version_reads += 1
        return await super().version(collection)


def chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("::")[0],
        title=chunk_id,
        url=f"https://example.test/{chunk_id}",
        domain="test",
        text=text,
        token_count=len(text.split()),
        content_sha256="payload-hash",
    )


@pytest.mark.asyncio
async def test_incremental_sync_embeds_only_changes(tmp_path) -> None:
    config = replace(
        settings,
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "state.sqlite3",
        qdrant_collection="test_chunks",
        embedding_dimensions=8,
    )
    embedder = FakeEmbedder()
    store = QdrantVectorStore(config, embedder, IndexStateRepository(config.runtime_db))
    await store.setup()
    try:
        first = await store.sync([chunk("a::c0000", "alpha"), chunk("b::c0000", "beta")])
        second = await store.sync([chunk("a::c0000", "alpha"), chunk("b::c0000", "beta")])
        third = await store.sync([chunk("a::c0000", "alpha changed")])
        assert first.embedded_chunks == 2
        assert second.embedded_chunks == 0
        assert third.embedded_chunks == 1
        assert third.deleted_chunks == 1
        assert embedder.embedded_texts == 3
        result = await store.search("alpha")
        assert len(result.hits) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_index_identity_includes_retrievable_payload_metadata(tmp_path) -> None:
    config = replace(
        settings,
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "state.sqlite3",
        qdrant_collection="test_payload_identity",
        embedding_dimensions=8,
    )
    embedder = FakeEmbedder()
    store = QdrantVectorStore(config, embedder, IndexStateRepository(config.runtime_db))
    await store.setup()
    try:
        original = chunk("a::c0000", "alpha")
        first = await store.sync([original])
        changed_url = replace(original, url="https://example.test/revised-source")
        second = await store.sync([changed_url])
        metadata = await store.state.metadata(config.qdrant_collection)

        assert second.embedded_chunks == 1
        assert second.index_checksum != first.index_checksum
        assert metadata["index_source_sha256"] is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_search_cache_isolated_by_scope_and_reports_fresh_metrics(tmp_path) -> None:
    config = replace(
        settings,
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "state.sqlite3",
        qdrant_collection="test_cache_scopes",
        embedding_dimensions=8,
    )
    embedder = SlowFakeEmbedder()
    state = CountingIndexStateRepository(config.runtime_db)
    store = QdrantVectorStore(config, embedder, state)
    await store.setup()
    try:
        await store.sync(
            [
                chunk("a::c0000", "alpha alpha"),
                chunk("b::c0000", "beta beta"),
            ]
        )
        after_sync = embedder.embedded_texts

        stream_miss = await store.search("Alpha", cache_scope="stream")
        stream_hit = await store.search("  alpha  ", cache_scope="stream")
        naive_miss = await store.search("alpha", cache_scope="naive")
        naive_hit = await store.search("ALPHA", cache_scope="naive")

        assert stream_miss.cache_scope == "stream"
        assert stream_miss.cache_hit is False
        assert stream_miss.embedding_tokens == 1
        assert stream_hit.cache_scope == "stream"
        assert stream_hit.cache_hit is True
        assert stream_hit.embedding_tokens == 0
        assert stream_hit.query == "alpha"
        assert stream_hit.elapsed_ms < stream_miss.elapsed_ms

        assert naive_miss.cache_scope == "naive"
        assert naive_miss.cache_hit is False
        assert naive_miss.embedding_tokens == 1
        assert naive_hit.cache_scope == "naive"
        assert naive_hit.cache_hit is True
        assert naive_hit.embedding_tokens == 0
        assert naive_hit.query == "ALPHA"
        assert naive_hit.elapsed_ms < naive_miss.elapsed_ms

        # Each path pays for its first query embedding; neither can reuse the other's work.
        assert embedder.embedded_texts - after_sync == 2
        assert [hit.chunk.chunk_id for hit in stream_hit.hits] == [
            hit.chunk.chunk_id for hit in stream_miss.hits
        ]
        assert [hit.rank for hit in stream_hit.hits] == [
            hit.rank for hit in stream_miss.hits
        ]
        assert [hit.score for hit in stream_hit.hits] == [
            hit.score for hit in stream_miss.hits
        ]
        assert [hit.chunk.chunk_id for hit in naive_hit.hits] == [
            hit.chunk.chunk_id for hit in naive_miss.hits
        ]
        assert state.version_reads == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_embedded_qdrant_work_does_not_block_the_event_loop(
    tmp_path, monkeypatch
) -> None:
    config = replace(
        settings,
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "state.sqlite3",
        qdrant_collection="test_non_blocking",
        embedding_dimensions=8,
    )
    store = QdrantVectorStore(
        config,
        FakeEmbedder(),
        IndexStateRepository(config.runtime_db),
    )
    await store.setup()
    original = store.client.get_collection

    def slow_get_collection(*args, **kwargs):
        time.sleep(0.08)
        return original(*args, **kwargs)

    monkeypatch.setattr(store.client, "get_collection", slow_get_collection)
    heartbeat_fired = asyncio.Event()

    async def heartbeat() -> None:
        await asyncio.sleep(0.01)
        heartbeat_fired.set()

    try:
        query = asyncio.create_task(store.get_collection())
        pulse = asyncio.create_task(heartbeat())
        await asyncio.wait_for(heartbeat_fired.wait(), timeout=0.04)
        await asyncio.gather(query, pulse)
    finally:
        await store.close()
