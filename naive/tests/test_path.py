from __future__ import annotations

import pytest

from naive.path import NaiveRagPath
from shared.config import Settings
from shared.models import InputSnapshot, SearchResult


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def search(self, query: str, *, cache_scope: str) -> SearchResult:
        self.calls.append((query, cache_scope))
        return SearchResult(
            query=query,
            hits=[],
            embedding_tokens=3,
            elapsed_ms=4.0,
            query_vector_ms=2.5,
            ann_ms=1.5,
            cache_scope=cache_scope,
        )


@pytest.mark.asyncio
async def test_commit_retrieves_exact_text_in_naive_scope() -> None:
    store = RecordingStore()
    path = NaiveRagPath(Settings(), store)  # type: ignore[arg-type]
    snapshot = InputSnapshot(turn_id="turn", revision=4, text="the complete question?")

    telemetry = await path.commit(
        snapshot=snapshot,
        session_id="session",
        committed_ms=1.0,
        turn=None,
    )

    assert store.calls == [("the complete question?", "session:naive")]
    assert telemetry.result.query == "the complete question?"
    assert telemetry.retrieval_calls == 1
    assert telemetry.controller_calls == 0
    assert telemetry.accepted_revision == 4
    assert telemetry.accepted_ready_before_commit is False
    assert telemetry.reuse_mode == "committed_text_retrieval"


@pytest.mark.asyncio
async def test_commit_bounds_oversized_exact_query() -> None:
    store = RecordingStore()
    path = NaiveRagPath(Settings(), store)  # type: ignore[arg-type]
    committed_text = f"start marker {'x' * 19_976} end marker"
    assert len(committed_text) == 20_000

    await path.commit(
        snapshot=InputSnapshot(turn_id="turn", revision=1, text=committed_text),
        session_id="session",
        committed_ms=1.0,
        turn=None,
    )

    query, scope = store.calls[0]
    assert len(query) == 2_000
    assert query.startswith("start marker")
    assert query.endswith("end marker")
    assert scope == "session:naive"


def test_naive_path_has_no_typed_turn_surface() -> None:
    path = NaiveRagPath(Settings(), RecordingStore())  # type: ignore[arg-type]
    assert path.name == "naive"
    assert path.supports_snapshots is False
    with pytest.raises(RuntimeError, match="does not accept"):
        path.open_turn(
            turn_id="turn",
            session_id="session",
            send=lambda _event: None,  # type: ignore[arg-type]
            conversation_context="",
        )


def test_naive_evaluation_schema_contains_no_stream_diagnostics() -> None:
    implementation = set(NaiveRagPath.evaluation_metrics["path_specific"])

    assert "retrieval.started_ms" in implementation
    assert not any(metric.startswith(("controller.", "reuse.")) for metric in implementation)
