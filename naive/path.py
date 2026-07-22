from __future__ import annotations

import asyncio
import time

from naive.evaluation import EVALUATION_METRICS
from shared.config import Settings
from shared.data.vector_store import QdrantVectorStore
from shared.models import InputSnapshot
from shared.path import EventSink, PathTelemetry, PathTurn, cache_scope
from shared.query import bounded_retrieval_query


class NaiveRagPath:
    name = "naive"
    supports_snapshots = False
    evaluation_metrics = EVALUATION_METRICS

    def __init__(self, settings: Settings, store: QdrantVectorStore):
        self.settings = settings
        self.store = store

    def public_metadata(self) -> dict[str, object]:
        return {}

    def open_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        send: EventSink,
        conversation_context: str,
    ) -> PathTurn:
        del turn_id, session_id, send, conversation_context
        raise RuntimeError("Naive RAG does not accept pre-Send snapshots")

    async def commit(
        self,
        *,
        snapshot: InputSnapshot,
        session_id: str,
        committed_ms: float,
        turn: PathTurn | None,
    ) -> PathTelemetry:
        del committed_ms
        if turn is not None:
            raise RuntimeError("Naive RAG commit received unexpected pre-Send state")
        query = bounded_retrieval_query(snapshot.text)
        started_ms = time.perf_counter() * 1000
        try:
            result = await asyncio.wait_for(
                self.store.search(
                    query,
                    cache_scope=cache_scope(session_id, self.name),
                ),
                timeout=self.settings.retrieval_timeout_s,
            )
        except TimeoutError:
            raise RuntimeError("committed-text retrieval timed out") from None
        completed_ms = time.perf_counter() * 1000
        attempt = {
            "retrieval_id": "retrieval-1",
            "origin": "direct_commit",
            "query": query[:300],
            "source_revision": snapshot.revision,
            "controller_validated": True,
            "commit_safe_exact": True,
            "created_ms": started_ms,
            "search_started_ms": started_ms,
            "finished_ms": completed_ms,
            "outcome": "ready",
            "cache_hit": result.cache_hit,
            "query_vector_cache_hit": result.query_vector_cache_hit,
            "search_cache_age_ms": result.search_cache_age_ms,
            "query_vector_ms": result.query_vector_ms,
            "ann_ms": result.ann_ms,
            "embedding_tokens": result.embedding_tokens,
        }
        return PathTelemetry(
            result=result,
            retrieval_started_ms=started_ms,
            retrieval_ready_ms=completed_ms,
            retrieval_calls=1,
            retrieval_embedding_tokens=result.embedding_tokens,
            retrieval_query_vector_ms=result.query_vector_ms,
            retrieval_ann_ms=result.ann_ms,
            accepted_revision=snapshot.revision,
            accepted_from_fallback=True,
            accepted_ready_before_commit=False,
            commit_branch="committed_text",
            retrieval_attempts=[attempt],
            timeline=[
                {"t_ms": started_ms, "type": "retrieval.started", "actor": "coordinator"},
                {"t_ms": completed_ms, "type": "retrieval.ready", "actor": "coordinator"},
            ],
            evidence_origin="direct_commit",
            accepted_query=query,
        )

    async def close(self) -> None:
        return None
