from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite


class IndexStateRepository:
    """Small durable metadata store; vectors themselves remain in Qdrant."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS index_state ("
                "collection TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0, "
                "index_checksum TEXT, desired_chunks INTEGER, index_source_sha256 TEXT)"
            )
            columns = {
                str(row[1])
                for row in await (await db.execute("PRAGMA table_info(index_state)")).fetchall()
            }
            if "index_checksum" not in columns:
                await db.execute("ALTER TABLE index_state ADD COLUMN index_checksum TEXT")
            if "desired_chunks" not in columns:
                await db.execute("ALTER TABLE index_state ADD COLUMN desired_chunks INTEGER")
            if "index_source_sha256" not in columns:
                await db.execute(
                    "ALTER TABLE index_state ADD COLUMN index_source_sha256 TEXT"
                )
            await db.commit()

    async def version(self, collection: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT version FROM index_state WHERE collection = ?", (collection,)
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def metadata(self, collection: str) -> dict[str, int | str | None]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT version, index_checksum, desired_chunks, index_source_sha256 "
                "FROM index_state "
                "WHERE collection = ?",
                (collection,),
            )
            row = await cursor.fetchone()
        if row is None:
            return {
                "version": 0,
                "index_checksum": None,
                "desired_chunks": None,
                "index_source_sha256": None,
            }
        return {
            "version": int(row[0]),
            "index_checksum": str(row[1]) if row[1] is not None else None,
            "desired_chunks": int(row[2]) if row[2] is not None else None,
            "index_source_sha256": str(row[3]) if row[3] is not None else None,
        }

    async def bump(self, collection: str) -> int:
        async with self._lock, aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT INTO index_state(collection, version) VALUES (?, 1) "
                "ON CONFLICT(collection) DO UPDATE SET version = version + 1",
                (collection,),
            )
            cursor = await db.execute(
                "SELECT version FROM index_state WHERE collection = ?", (collection,)
            )
            row = await cursor.fetchone()
            await db.commit()
            return int(row[0])

    async def record_sync(
        self,
        collection: str,
        *,
        index_checksum: str,
        index_source_sha256: str,
        desired_chunks: int,
        content_changed: bool,
    ) -> int:
        """Persist the exact desired-index identity, bumping only for content changes."""
        async with self._lock, aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT version, index_source_sha256 FROM index_state WHERE collection = ?",
                (collection,),
            )
            row = await cursor.fetchone()
            source_changed = row is None or row[1] != index_source_sha256
            version = (int(row[0]) if row else 0) + int(content_changed or source_changed)
            await db.execute(
                "INSERT INTO index_state(collection, version, index_checksum, desired_chunks, "
                "index_source_sha256) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(collection) DO UPDATE SET "
                "version = excluded.version, index_checksum = excluded.index_checksum, "
                "desired_chunks = excluded.desired_chunks, "
                "index_source_sha256 = excluded.index_source_sha256",
                (
                    collection,
                    version,
                    index_checksum,
                    desired_chunks,
                    index_source_sha256,
                ),
            )
            await db.commit()
            return version
