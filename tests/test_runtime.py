from __future__ import annotations

import asyncio

import pytest

from app.api.runtime import RagRuntime, TurnClosedError, TurnConflictError
from app.api.schemas import CommitRequest, SnapshotRequest
from app.config import Settings
from app.models import InputSnapshot, SearchResult, TriggerDecision, Usage
from app.stream.trigger import TriggerResult


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
            cache_scope=cache_scope,
        )


class RecordingTrigger:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    async def decide(self, **kwargs) -> TriggerResult:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("controller unavailable")
        return TriggerResult(
            decision=TriggerDecision(
                action="retrieve",
                retrieval_query="standalone controller query",
            ),
            usage=Usage(input_tokens=7, output_tokens=2, calls=1, names=["trigger"]),
            elapsed_ms=5.0,
        )


def runtime(store: RecordingStore, trigger: RecordingTrigger) -> RagRuntime:
    return RagRuntime(
        settings=Settings(),
        store=store,  # type: ignore[arg-type]
        agent=object(),  # type: ignore[arg-type]
        trigger=trigger,  # type: ignore[arg-type]
        logger=object(),  # type: ignore[arg-type]
    )


class ConversationAgentStub:
    async def conversation_context(self, _session_key: str) -> str:
        return ""


@pytest.mark.asyncio
async def test_unrelated_turn_context_reads_do_not_share_the_registry_lock() -> None:
    class ConcurrentConversationAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
            self.release = asyncio.Event()

        async def conversation_context(self, _session_key: str) -> str:
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
            else:
                self.second_started.set()
            await self.release.wait()
            return ""

    subject = runtime(RecordingStore(), RecordingTrigger())
    agent = ConcurrentConversationAgent()
    subject.agent = agent  # type: ignore[assignment]
    first = asyncio.create_task(
        subject.accept_snapshot(
            "turn-a",
            SnapshotRequest(
                session_id="session-a",
                path="stream",
                revision=1,
                text="first partial",
            ),
        )
    )
    await agent.first_started.wait()
    second = asyncio.create_task(
        subject.accept_snapshot(
            "turn-b",
            SnapshotRequest(
                session_id="session-b",
                path="stream",
                revision=1,
                text="second partial",
            ),
        )
    )

    await asyncio.wait_for(agent.second_started.wait(), timeout=0.5)
    agent.release.set()
    await asyncio.gather(first, second)
    await subject.cancel_turn("turn-a")
    await subject.cancel_turn("turn-b")


@pytest.mark.asyncio
async def test_endpoint_policy_is_identical_but_cache_scoped_per_path() -> None:
    store = RecordingStore()
    trigger = RecordingTrigger()
    subject = runtime(store, trigger)
    committed = InputSnapshot(turn_id="turn", revision=4, text="the complete question?")

    naive = await subject._endpoint_retrieve(
        committed,
        session_id="session",
        path="naive",
        conversation_context="same history",
    )
    stream = await subject._endpoint_retrieve(
        committed,
        session_id="session",
        path="stream",
        conversation_context="same history",
    )

    assert store.calls == [
        ("standalone controller query", "session:naive"),
        ("standalone controller query", "session:stream"),
    ]
    assert [call["is_commit"] for call in trigger.calls] == [True, True]
    assert [call["previous_query"] for call in trigger.calls] == [None, None]
    assert naive.controller_calls == stream.controller_calls == 1
    assert naive.controller_usage.calls == stream.controller_usage.calls == 1


@pytest.mark.asyncio
async def test_endpoint_controller_failure_falls_back_to_exact_committed_text() -> None:
    store = RecordingStore()
    subject = runtime(store, RecordingTrigger(fail=True))
    committed = InputSnapshot(turn_id="turn", revision=1, text="exact committed text")

    result = await subject._endpoint_retrieve(
        committed,
        session_id="session",
        path="naive",
        conversation_context="",
    )

    assert store.calls == [("exact committed text", "session:naive")]
    assert result.controller_calls == 1
    assert result.controller_failures == 1
    assert result.controller_timeouts == 0


@pytest.mark.asyncio
async def test_complete_input_plan_can_confirm_previous_stream_query() -> None:
    class KeepPreviousTrigger(RecordingTrigger):
        async def decide(self, **kwargs) -> TriggerResult:
            self.calls.append(kwargs)
            return TriggerResult(
                decision=TriggerDecision(action="keep_previous"),
                usage=Usage(input_tokens=5, output_tokens=1, calls=1),
                elapsed_ms=2.0,
            )

    trigger = KeepPreviousTrigger()
    subject = runtime(RecordingStore(), trigger)
    committed = InputSnapshot(turn_id="turn", revision=2, text="complete question?")

    plan = await subject._endpoint_plan(
        committed,
        previous_query="stable retrieval query",
        conversation_context="",
    )

    assert plan.query == "stable retrieval query"
    assert plan.reused_previous is True
    assert plan.decision_action == "keep_previous"
    assert trigger.calls[0]["is_commit"] is True


@pytest.mark.asyncio
async def test_cancel_turn_cancels_associated_run_tasks() -> None:
    subject = runtime(RecordingStore(), RecordingTrigger())
    started = asyncio.Event()

    async def long_run() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(long_run())
    subject.tasks.add(task)
    subject.turn_tasks["turn"] = {task}
    await started.wait()

    await subject.cancel_turn("turn")

    assert task.cancelled()
    assert "turn" not in subject.turn_tasks


@pytest.mark.asyncio
async def test_commit_is_idempotent_and_late_snapshots_cannot_recreate_turn() -> None:
    subject = runtime(RecordingStore(), RecordingTrigger())
    subject.agent = ConversationAgentStub()  # type: ignore[assignment]
    execution_started = asyncio.Event()

    async def held_execute(*_args, **_kwargs) -> None:
        execution_started.set()
        await asyncio.Event().wait()

    subject._execute = held_execute  # type: ignore[method-assign]
    snapshot = SnapshotRequest(
        session_id="session",
        path="stream",
        revision=1,
        text="partial",
    )
    request = CommitRequest(
        session_id="session",
        path="stream",
        revision=2,
        text="partial question?",
        query_time="2026-01-01T00:00:00Z",
        client_ts_ms=10.0,
    )
    await subject.accept_snapshot("turn", snapshot)

    first_run = await subject.start_commit("turn", request)
    duplicate_run = await subject.start_commit(
        "turn",
        request.model_copy(
            update={"client_ts_ms": 99.0, "query_time": "2026-01-01T00:00:01Z"}
        ),
    )
    await execution_started.wait()

    assert duplicate_run == first_run
    assert subject.counters.runs_started == 1
    with pytest.raises(TurnClosedError):
        await subject.accept_snapshot(
            "turn",
            snapshot.model_copy(update={"revision": 3, "text": "late snapshot"}),
        )
    with pytest.raises(TurnConflictError):
        await subject.start_commit(
            "turn",
            request.model_copy(update={"text": "different question?"}),
        )
    await subject.cancel_turn("turn")


@pytest.mark.asyncio
async def test_turn_id_cannot_cross_session_or_path_binding() -> None:
    subject = runtime(RecordingStore(), RecordingTrigger())
    subject.agent = ConversationAgentStub()  # type: ignore[assignment]
    await subject.accept_snapshot(
        "turn",
        SnapshotRequest(
            session_id="session-a",
            path="stream",
            revision=1,
            text="partial",
        ),
    )

    with pytest.raises(TurnConflictError):
        await subject.accept_snapshot(
            "turn",
            SnapshotRequest(
                session_id="session-b",
                path="compare",
                revision=2,
                text="partial question",
            ),
        )
    await subject.cancel_turn("turn")


@pytest.mark.asyncio
async def test_idle_uncommitted_turn_is_reaped() -> None:
    subject = runtime(RecordingStore(), RecordingTrigger())
    subject.settings = Settings(turn_idle_timeout_s=1)
    subject.agent = ConversationAgentStub()  # type: ignore[assignment]
    await subject.accept_snapshot(
        "idle-turn",
        SnapshotRequest(
            session_id="session",
            path="stream",
            revision=1,
            text="partial",
        ),
    )
    subject.turns["idle-turn"].last_activity_ms = 100.0

    reaped = await subject.reap_idle_turns(now_ms=2_000.0)

    assert reaped == 1
    assert "idle-turn" not in subject.turns
    assert await subject.events.existing("turn:idle-turn") is None


@pytest.mark.asyncio
async def test_failed_commit_setup_rolls_back_and_duplicate_gets_no_phantom_run() -> None:
    class FailingConversationAgent:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def conversation_context(self, _session_key: str) -> str:
            self.started.set()
            await self.release.wait()
            raise RuntimeError("conversation store unavailable")

    subject = runtime(RecordingStore(), RecordingTrigger())
    failing_agent = FailingConversationAgent()
    subject.agent = failing_agent  # type: ignore[assignment]
    request = CommitRequest(
        session_id="session",
        path="stream",
        revision=1,
        text="complete question?",
    )
    first = asyncio.create_task(subject.start_commit("turn", request))
    await failing_agent.started.wait()
    duplicate = asyncio.create_task(subject.start_commit("turn", request))
    failing_agent.release.set()

    with pytest.raises(RuntimeError, match="conversation store unavailable"):
        await first
    with pytest.raises(RuntimeError, match="original commit setup failed"):
        await duplicate
    assert "turn" not in subject.terminal_turns
    assert subject.counters.runs_started == 0

    execution_started = asyncio.Event()

    async def held_execute(*_args, **_kwargs) -> None:
        execution_started.set()
        await asyncio.Event().wait()

    subject.agent = ConversationAgentStub()  # type: ignore[assignment]
    subject._execute = held_execute  # type: ignore[method-assign]
    run_id = await subject.start_commit("turn", request)
    await execution_started.wait()
    assert run_id
    assert subject.terminal_turns["turn"].started is True  # type: ignore[union-attr]
    await subject.cancel_turn("turn")


@pytest.mark.asyncio
async def test_cancel_is_atomic_with_blocked_commit_setup() -> None:
    class BlockingConversationAgent:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def conversation_context(self, _session_key: str) -> str:
            self.started.set()
            await self.release.wait()
            return ""

    subject = runtime(RecordingStore(), RecordingTrigger())
    agent = BlockingConversationAgent()
    subject.agent = agent  # type: ignore[assignment]

    async def held_execute(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    subject._execute = held_execute  # type: ignore[method-assign]
    request = CommitRequest(
        session_id="session",
        path="stream",
        revision=1,
        text="complete question?",
    )
    committing = asyncio.create_task(subject.start_commit("turn", request))
    await agent.started.wait()
    cancelling = asyncio.create_task(subject.cancel_turn("turn"))
    agent.release.set()
    results = await asyncio.gather(committing, cancelling, return_exceptions=True)

    assert results[1] is None
    assert subject.terminal_turns["turn"] is None
    assert "turn" not in subject.turn_tasks
    assert not subject.tasks
    with pytest.raises(TurnClosedError):
        await subject.accept_snapshot(
            "turn",
            SnapshotRequest(
                session_id="session",
                path="stream",
                revision=2,
                text="late snapshot",
            ),
        )
