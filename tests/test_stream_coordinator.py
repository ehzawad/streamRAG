from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.models import InputSnapshot, SearchResult, TriggerDecision, Usage
from app.stream.coordinator import (
    RetrievalEvidence,
    StreamCoordinator,
)
from app.stream.trigger import TriggerResult


def snapshot(revision: int, text: str) -> InputSnapshot:
    return InputSnapshot(turn_id="turn-1", revision=revision, text=text)


def search_result(query: str, *, cache_scope: str = "stream") -> SearchResult:
    return SearchResult(
        query=query,
        hits=[],
        embedding_tokens=1,
        elapsed_ms=2.0,
        cache_scope=cache_scope,
    )


class FakeIndex:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.release = asyncio.Event()
        self.release.set()

    async def search(self, query: str, *, cache_scope: str = "default") -> SearchResult:
        self.calls.append((query, cache_scope))
        await self.release.wait()
        return search_result(query, cache_scope=cache_scope)


class ControlledTrigger:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0

    async def decide(
        self,
        *,
        draft: str,
        previous_query: str | None,
        conversation_context: str,
    ) -> TriggerResult:
        del previous_query, conversation_context
        self.calls.append(draft)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if len(self.calls) == 1:
            decision = TriggerDecision(action="retrieve", retrieval_query=draft)
        else:
            decision = TriggerDecision(action="wait")
        return TriggerResult(decision=decision, usage=Usage(calls=1), elapsed_ms=1.0)


class SequencedTrigger:
    def __init__(self, *decisions: TriggerDecision) -> None:
        self.decisions = list(decisions)
        self.calls: list[tuple[str, str | None]] = []

    async def decide(
        self,
        *,
        draft: str,
        previous_query: str | None,
        conversation_context: str,
    ) -> TriggerResult:
        del conversation_context
        self.calls.append((draft, previous_query))
        return TriggerResult(
            decision=self.decisions.pop(0),
            usage=Usage(calls=1),
            elapsed_ms=1.0,
        )


async def wait_until(predicate, message: str) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(message)


async def make_coordinator(
    *,
    trigger=None,
    index=None,
    trigger_timeout_s: float = 1.0,
    retrieval_timeout_s: float = 1.0,
    parallel_raw_retrieval: bool = False,
    settled_draft_delay_ms: int = 500,
):
    events: list[dict] = []

    async def send(event: dict) -> None:
        events.append(event)

    coordinator = StreamCoordinator(
        turn_id="turn-1",
        index=index or FakeIndex(),
        trigger=trigger or ControlledTrigger(),
        settings=Settings(
            trigger_min_tokens=1,
            trigger_min_new_tokens=1,
            trigger_interval_ms=0,
            trigger_max_presubmit_calls=4,
            parallel_raw_retrieval=parallel_raw_retrieval,
            settled_draft_delay_ms=settled_draft_delay_ms,
            trigger_timeout_s=trigger_timeout_s,
            retrieval_timeout_s=retrieval_timeout_s,
        ),
        send=send,
        cache_scope="stream:test",
    )
    return coordinator, events


@pytest.mark.asyncio
async def test_quiet_draft_retrieves_exact_text_without_waiting_for_model() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()
    coordinator, events = await make_coordinator(
        trigger=trigger,
        index=index,
        settled_draft_delay_ms=1,
    )
    draft = snapshot(1, "who wrote dune")

    await coordinator.update(draft)
    await wait_until(
        lambda: coordinator.evidence is not None,
        "settled exact retrieval did not complete",
    )

    assert trigger.cancelled == 1
    assert index.calls == [(draft.text, "stream:test")]
    assert coordinator.evidence is not None
    assert coordinator.evidence.controller_validated is False
    assert coordinator.evidence.commit_safe_exact is True
    assert coordinator.metrics.settled_draft_retrievals == 1
    assert any(event["type"] == "draft.settled" for event in events)
    assert any(
        event["type"] == "retrieval.ready"
        and event["candidate"] is False
        and event["commit_safe_exact"] is True
        for event in events
    )
    assert all(not event["type"].startswith("answer.") for event in events)

    evidence = await coordinator.commit(snapshot(2, draft.text))

    assert evidence is coordinator.evidence
    assert coordinator.metrics.evidence_reuses == 1
    assert coordinator.metrics.commit_fallbacks == 0
    assert coordinator.metrics.accepted_ready_before_commit is True
    await coordinator.close()


@pytest.mark.asyncio
async def test_quiet_draft_does_not_relabel_a_rewritten_controller_query_as_exact() -> None:
    index = FakeIndex()
    coordinator, _ = await make_coordinator(index=index)
    draft = snapshot(1, "who wrote dune")
    coordinator.latest = draft
    coordinator.evidence = RetrievalEvidence(
        source_text=draft.text,
        revision=draft.revision,
        query="DUNE AUTHOR",
        result=search_result("DUNE AUTHOR"),
        started_ms=1.0,
        completed_ms=2.0,
        controller_validated=True,
    )

    await coordinator._start_settled_draft_retrieval(draft)
    await wait_until(
        lambda: coordinator.evidence is not None and coordinator.evidence.query == draft.text,
        "exact settled retrieval did not replace the rewritten query",
    )

    assert index.calls == [(draft.text, "stream:test")]
    assert coordinator.evidence is not None
    assert coordinator.evidence.commit_safe_exact is True
    await coordinator.close()


@pytest.mark.asyncio
async def test_new_typing_rearms_quiet_retrieval_for_latest_text() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()
    coordinator, _ = await make_coordinator(
        trigger=trigger,
        index=index,
        settled_draft_delay_ms=20,
    )

    await coordinator.update(snapshot(1, "who wrote dune"))
    await asyncio.sleep(0.005)
    latest = snapshot(2, "who wrote dune messiah")
    await coordinator.update(latest)
    await asyncio.sleep(0.025)
    await wait_until(lambda: bool(index.calls), "latest settled retrieval did not start")

    assert index.calls == [(latest.text, "stream:test")]
    await coordinator.close()


@pytest.mark.asyncio
async def test_stale_post_ack_snapshot_cannot_replace_latest_quiet_timer() -> None:
    first_ack_started = asyncio.Event()
    release_first_ack = asyncio.Event()
    events: list[dict] = []

    async def send(event: dict) -> None:
        if event.get("type") == "input.ack" and event.get("revision") == 1:
            first_ack_started.set()
            await release_first_ack.wait()
        events.append(event)

    index = FakeIndex()
    coordinator = StreamCoordinator(
        turn_id="turn-1",
        index=index,
        trigger=ControlledTrigger(),
        settings=Settings(
            trigger_min_tokens=1,
            trigger_min_new_tokens=1,
            trigger_interval_ms=0,
            trigger_max_presubmit_calls=4,
            parallel_raw_retrieval=False,
            settled_draft_delay_ms=1,
            trigger_timeout_s=1.0,
            retrieval_timeout_s=1.0,
        ),
        send=send,
        cache_scope="stream:test",
    )

    first = asyncio.create_task(coordinator.update(snapshot(1, "who wrote dune")))
    await first_ack_started.wait()
    latest = snapshot(2, "who wrote dune messiah")
    await coordinator.update(latest)
    await wait_until(lambda: bool(index.calls), "latest quiet retrieval did not start")
    release_first_ack.set()
    await first

    assert index.calls == [(latest.text, "stream:test")]
    assert any(event.get("type") == "draft.settled" for event in events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_inflight_settled_retrieval_continues_across_send() -> None:
    index = FakeIndex()
    index.release.clear()
    coordinator, _ = await make_coordinator(
        index=index,
        settled_draft_delay_ms=1,
    )
    draft = snapshot(1, "when was dune published")

    await coordinator.update(draft)
    await wait_until(lambda: bool(index.calls), "settled retrieval did not start")
    committed = snapshot(2, draft.text)
    commit_task = asyncio.create_task(coordinator.commit(committed))
    await asyncio.sleep(0)
    assert not commit_task.done()

    index.release.set()
    evidence = await commit_task

    assert evidence.source_text == committed.text
    assert evidence.commit_safe_exact is True
    assert coordinator.metrics.commit_fallbacks == 0
    assert coordinator.metrics.accepted_ready_before_commit is False
    assert index.calls == [(draft.text, "stream:test")]
    await coordinator.close()


@pytest.mark.asyncio
async def test_settled_event_is_published_only_after_retrieval_is_installed() -> None:
    settled_publish_started = asyncio.Event()
    hold_settled_publish = asyncio.Event()

    async def send(event: dict) -> None:
        if event.get("type") == "draft.settled":
            settled_publish_started.set()
            await hold_settled_publish.wait()

    index = FakeIndex()
    index.release.clear()
    coordinator = StreamCoordinator(
        turn_id="turn-1",
        index=index,
        trigger=ControlledTrigger(),
        settings=Settings(
            trigger_min_tokens=1,
            trigger_min_new_tokens=1,
            trigger_interval_ms=0,
            trigger_max_presubmit_calls=4,
            parallel_raw_retrieval=False,
            settled_draft_delay_ms=1,
            trigger_timeout_s=1.0,
            retrieval_timeout_s=1.0,
        ),
        send=send,
        cache_scope="stream:test",
    )
    draft = snapshot(1, "when was dune published")

    await coordinator.update(draft)
    await settled_publish_started.wait()

    assert coordinator._retrieval_task is not None
    await wait_until(lambda: bool(index.calls), "settled retrieval did not enter the index")
    committed = snapshot(2, draft.text)
    commit_task = asyncio.create_task(coordinator.commit(committed))
    await asyncio.sleep(0)
    index.release.set()
    evidence = await commit_task

    assert evidence.commit_safe_exact is True
    assert coordinator.metrics.commit_fallbacks == 0
    await coordinator.close()


@pytest.mark.asyncio
async def test_material_suffix_cannot_reuse_settled_exact_evidence() -> None:
    index = FakeIndex()
    coordinator, _ = await make_coordinator(
        index=index,
        settled_draft_delay_ms=1,
    )
    prefix = snapshot(1, "who won")
    await coordinator.update(prefix)
    await wait_until(
        lambda: coordinator.evidence is not None,
        "prefix settled retrieval did not complete",
    )

    committed = snapshot(2, "who won in 2024?")
    evidence = await coordinator.commit(committed)

    assert evidence.source_text == committed.text
    assert coordinator.metrics.commit_fallbacks == 1
    assert index.calls == [
        (prefix.text, "stream:test"),
        (committed.text, "stream:test"),
    ]
    await coordinator.close()


@pytest.mark.asyncio
async def test_material_last_token_completion_uses_exact_commit_fallback() -> None:
    query = "dune publication year"
    trigger = SequencedTrigger(
        TriggerDecision(action="retrieve", retrieval_query=query),
    )

    index = FakeIndex()
    coordinator, events = await make_coordinator(trigger=trigger, index=index)
    prefix = snapshot(1, "when was dune publi")
    committed = snapshot(2, "when was dune published?")
    await coordinator.update(prefix)
    await wait_until(lambda: coordinator.evidence is not None, "prefix retrieval missing")

    evidence = await coordinator.commit(committed)

    assert index.calls == [
        (query, "stream:test"),
        (committed.text, "stream:test"),
    ]
    assert evidence.source_text == committed.text
    assert evidence.query == committed.text
    assert coordinator.metrics.accepted_ready_before_commit is False
    assert coordinator.metrics.evidence_reuses == 0
    assert coordinator.metrics.commit_fallbacks == 1
    assert any(event["type"] == "retrieval.fallback" for event in events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_raw_retrieval_overlaps_trigger_and_is_promoted_before_commit() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()
    coordinator, events = await make_coordinator(
        trigger=trigger,
        index=index,
        parallel_raw_retrieval=True,
    )
    complete = snapshot(1, "when was dune published ")

    await coordinator.update(complete)
    await asyncio.wait_for(trigger.started.wait(), timeout=0.1)
    await wait_until(lambda: coordinator.evidence is not None, "raw retrieval missing")

    assert not trigger.release.is_set()
    assert index.calls == [("when was dune published", "stream:test")]
    assert coordinator.evidence is not None
    assert coordinator.evidence.controller_validated is False
    assert coordinator.metrics.raw_retrieval_calls == 1
    assert any(
        event["type"] == "retrieval.ready" and event["candidate"] is True for event in events
    )

    trigger.release.set()
    await wait_until(
        lambda: coordinator.evidence is not None and coordinator.evidence.controller_validated,
        "raw retrieval was not promoted",
    )
    evidence = await coordinator.commit(complete)

    assert evidence.controller_validated is True
    assert coordinator.metrics.accepted_ready_before_commit is True
    assert coordinator.metrics.evidence_reuses == 1
    assert coordinator.metrics.commit_fallbacks == 0
    assert index.calls == [("when was dune published", "stream:test")]
    await coordinator.close()


@pytest.mark.asyncio
async def test_commit_never_accepts_unvalidated_raw_evidence() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()

    coordinator, _ = await make_coordinator(
        trigger=trigger,
        index=index,
        parallel_raw_retrieval=True,
    )
    complete = snapshot(1, "when was dune published ")
    await coordinator.update(complete)
    await wait_until(lambda: coordinator.evidence is not None, "raw retrieval missing")
    assert coordinator.evidence is not None
    assert coordinator.evidence.controller_validated is False

    evidence = await coordinator.commit(complete)

    assert trigger.cancelled == 1
    assert evidence.controller_validated is True
    assert coordinator.metrics.accepted_ready_before_commit is False
    assert coordinator.metrics.evidence_reuses == 0
    assert coordinator.metrics.commit_fallbacks == 1
    assert index.calls == [
        ("when was dune published", "stream:test"),
        ("when was dune published", "stream:test"),
    ]
    await coordinator.close()


@pytest.mark.asyncio
async def test_wait_discards_raw_candidate_without_promotion() -> None:
    class WaitingTrigger(ControlledTrigger):
        async def decide(self, **kwargs) -> TriggerResult:
            self.calls.append(kwargs["draft"])
            self.started.set()
            await self.release.wait()
            return TriggerResult(
                decision=TriggerDecision(action="wait"),
                usage=Usage(calls=1),
                elapsed_ms=1.0,
            )

    trigger = WaitingTrigger()
    coordinator, events = await make_coordinator(
        trigger=trigger,
        parallel_raw_retrieval=True,
    )

    await coordinator.update(snapshot(1, "what was mercury discovered as "))
    await wait_until(lambda: coordinator.evidence is not None, "raw retrieval missing")
    assert coordinator.evidence is not None
    assert coordinator.evidence.controller_validated is False
    trigger.release.set()
    await wait_until(
        lambda: coordinator.evidence is None and coordinator._retrieval_task is None,
        "wait did not discard raw evidence",
    )

    assert coordinator.previous_query is None
    assert coordinator.metrics.evidence_revalidations == 0
    assert not any(event["type"] == "retrieval.revalidated" for event in events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_rewritten_query_replaces_completed_raw_candidate() -> None:
    class RewriteTrigger(ControlledTrigger):
        async def decide(self, **kwargs) -> TriggerResult:
            self.calls.append(kwargs["draft"])
            self.started.set()
            await self.release.wait()
            return TriggerResult(
                decision=TriggerDecision(
                    action="retrieve",
                    retrieval_query="planet Mercury discovery history",
                ),
                usage=Usage(calls=1),
                elapsed_ms=1.0,
            )

    trigger = RewriteTrigger()
    index = FakeIndex()
    coordinator, _ = await make_coordinator(
        trigger=trigger,
        index=index,
        parallel_raw_retrieval=True,
    )

    await coordinator.update(snapshot(1, "when was mercury discovered "))
    await wait_until(lambda: coordinator.evidence is not None, "raw retrieval missing")
    trigger.release.set()
    await wait_until(
        lambda: (
            coordinator.evidence is not None
            and coordinator.evidence.query == "planet Mercury discovery history"
        ),
        "rewritten retrieval missing",
    )

    assert index.calls == [
        ("when was mercury discovered", "stream:test"),
        ("planet Mercury discovery history", "stream:test"),
    ]
    assert coordinator.evidence is not None
    assert coordinator.evidence.controller_validated is True
    assert coordinator.metrics.raw_retrieval_calls == 1
    assert coordinator.metrics.retrieval_calls == 2
    await coordinator.close()


@pytest.mark.asyncio
async def test_compatible_candidate_wins_over_optional_rewrite() -> None:
    class CompatibleTrigger(ControlledTrigger):
        async def decide(self, **kwargs) -> TriggerResult:
            self.calls.append(kwargs["draft"])
            self.started.set()
            await self.release.wait()
            return TriggerResult(
                decision=TriggerDecision(
                    action="retrieve",
                    candidate_query_compatible=True,
                    retrieval_query="cleaner dune publication query",
                ),
                usage=Usage(calls=1),
                elapsed_ms=1.0,
            )

    trigger = CompatibleTrigger()
    index = FakeIndex()
    coordinator, _ = await make_coordinator(
        trigger=trigger,
        index=index,
        parallel_raw_retrieval=True,
    )

    await coordinator.update(snapshot(1, "when was dune published "))
    await wait_until(lambda: coordinator.evidence is not None, "raw retrieval missing")
    trigger.release.set()
    await wait_until(
        lambda: coordinator.evidence is not None and coordinator.evidence.controller_validated,
        "compatible candidate was not promoted",
    )

    assert index.calls == [("when was dune published", "stream:test")]
    assert coordinator.previous_query == "when was dune published"
    assert coordinator.evidence is not None
    assert coordinator.evidence.query == "when was dune published"
    assert coordinator.metrics.retrieval_calls == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_long_queries_receive_bounded_adaptive_controller_budget() -> None:
    coordinator, _ = await make_coordinator()

    coordinator.metrics.trigger_calls = 4
    assert not coordinator._within_call_budget(snapshot(1, "one two three four five six seven"))
    assert coordinator._within_call_budget(
        snapshot(2, "one two three four five six seven eight nine ten eleven twelve thirteen")
    )

    coordinator.metrics.trigger_calls = 7
    very_long = " ".join(f"word{number}" for number in range(30))
    assert not coordinator._within_call_budget(snapshot(3, very_long))
    assert coordinator._within_call_budget(snapshot(4, very_long + "?"))

    coordinator.metrics.trigger_calls = 8
    assert not coordinator._within_call_budget(snapshot(5, very_long + "?"))
    await coordinator.close()


@pytest.mark.asyncio
async def test_server_commit_freezes_revision_before_late_snapshot_arrives() -> None:
    coordinator, events = await make_coordinator()
    committed = snapshot(5, "the final committed question?")

    coordinator.freeze_at_commit(committed, committed_ms=1234.5)
    await coordinator.update(snapshot(4, "a late lower revision"))
    await coordinator.update(snapshot(6, "a late higher revision"))

    assert coordinator.latest == committed
    assert coordinator.metrics.commit_ms == 1234.5
    assert events == []
    await coordinator.close()


@pytest.mark.asyncio
async def test_update_suspended_in_ack_cannot_launch_work_after_commit_freeze() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()
    coordinator, _ = await make_coordinator(
        trigger=trigger,
        index=index,
        parallel_raw_retrieval=True,
    )
    ack_started = asyncio.Event()
    release_ack = asyncio.Event()

    async def blocked_send(event: dict) -> None:
        if event["type"] == "input.ack":
            ack_started.set()
            await release_ack.wait()

    coordinator.send = blocked_send
    committed = snapshot(1, "what year was the novel dune first published?")
    updating = asyncio.create_task(coordinator.update(committed))
    await ack_started.wait()

    coordinator.freeze_at_commit(committed)
    release_ack.set()
    await updating
    await asyncio.sleep(0)

    assert coordinator.committed is True
    assert trigger.calls == []
    assert index.calls == []
    assert coordinator.metrics.trigger_calls == 0
    assert coordinator.metrics.retrieval_calls == 0
    await coordinator.close()


@pytest.mark.asyncio
async def test_pending_trigger_is_not_launched_after_commit_freeze() -> None:
    trigger = ControlledTrigger()
    coordinator, _ = await make_coordinator(trigger=trigger)

    await coordinator.update(snapshot(1, "one two three"))
    await trigger.started.wait()
    await coordinator.update(snapshot(2, "one two three four five six"))
    coordinator.freeze_at_commit(snapshot(3, "one two three four five six seven?"))
    trigger.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(trigger.calls) == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_commit_reuses_exact_evidence_before_waiting_for_active_work() -> None:
    coordinator, events = await make_coordinator()
    committed = snapshot(2, "who wrote dune?")
    coordinator.latest = committed
    coordinator.evidence = RetrievalEvidence(
        source_text=committed.text,
        revision=committed.revision,
        query="who wrote dune",
        result=search_result("who wrote dune"),
        started_ms=10.0,
        completed_ms=20.0,
    )
    blocker = asyncio.Event()
    coordinator._trigger_task = asyncio.create_task(blocker.wait())

    evidence = await asyncio.wait_for(coordinator.commit(committed), timeout=0.1)

    assert evidence is coordinator.evidence
    assert coordinator.metrics.evidence_reuses == 1
    assert coordinator.metrics.commit_fallbacks == 0
    assert coordinator.metrics.accepted_retrieval_started_ms == 10.0
    assert coordinator.metrics.accepted_retrieval_ready_ms == 20.0
    assert any(event["type"] == "retrieval.reused" for event in events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_commit_never_waits_for_an_unfinished_model_decision() -> None:
    coordinator, _ = await make_coordinator()
    committed = snapshot(1, "who wrote dune?")
    coordinator.latest = committed
    blocker = asyncio.Event()
    trigger_task = asyncio.create_task(blocker.wait())
    coordinator._trigger_task = trigger_task

    evidence = await asyncio.wait_for(coordinator.commit(committed), timeout=0.1)

    assert evidence.source_text == committed.text
    assert coordinator.metrics.commit_fallbacks == 1
    assert coordinator.metrics.evidence_reuses == 0
    assert trigger_task.cancelled()
    await coordinator.close()


@pytest.mark.asyncio
async def test_append_only_revision_does_not_cancel_or_discard_active_trigger() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()
    coordinator, _ = await make_coordinator(trigger=trigger, index=index)

    await coordinator.update(snapshot(1, "who is older tom cruise"))
    await asyncio.wait_for(trigger.started.wait(), timeout=0.1)
    first_task = coordinator._trigger_task
    await coordinator.update(snapshot(2, "who is older tom cruise or cher"))

    assert coordinator._trigger_task is first_task
    assert not first_task.cancelled()
    assert coordinator.metrics.trigger_cancellations == 0

    trigger.release.set()
    for _ in range(20):
        if coordinator.evidence is not None:
            break
        await asyncio.sleep(0)

    assert coordinator.evidence is not None
    assert coordinator.evidence.revision == 1
    assert coordinator.latest.text.startswith(coordinator.evidence.source_text)
    assert coordinator.metrics.stale_discards == 0
    assert coordinator.metrics.retrieval_cancellations == 0
    assert index.calls == [("who is older tom cruise", "stream:test")]
    await coordinator.close()


@pytest.mark.asyncio
async def test_correction_invalidates_evidence_and_cancels_stale_trigger() -> None:
    trigger = ControlledTrigger()
    coordinator, _ = await make_coordinator(trigger=trigger)
    coordinator.evidence = RetrievalEvidence(
        source_text="who is older tom cruise",
        revision=1,
        query="tom cruise age",
        result=search_result("tom cruise age"),
        started_ms=1.0,
        completed_ms=2.0,
    )
    coordinator.previous_query = "tom cruise age"

    await coordinator.update(snapshot(1, "who is older tom cruise"))
    await asyncio.wait_for(trigger.started.wait(), timeout=0.1)
    await coordinator.update(snapshot(2, "who is older brad pitt"))
    await asyncio.sleep(0)

    assert trigger.cancelled == 1
    assert coordinator.metrics.trigger_cancellations == 1
    assert coordinator.metrics.unpriced_trigger_cancellations == 1
    assert coordinator.metrics.trigger_usage.calls == 0
    assert coordinator.evidence is None
    assert coordinator.previous_query is None
    assert coordinator.latest.text == "who is older brad pitt"
    trigger.release.set()
    await coordinator.close()


@pytest.mark.asyncio
async def test_controller_cancel_after_usage_is_not_marked_unpriced() -> None:
    decision_started = asyncio.Event()
    release_decision = asyncio.Event()

    async def blocked_send(event: dict) -> None:
        if event["type"] == "trigger.decision":
            decision_started.set()
            await release_decision.wait()

    trigger = SequencedTrigger(
        TriggerDecision(action="retrieve", retrieval_query="dune publication year")
    )
    coordinator, _ = await make_coordinator(trigger=trigger)
    coordinator.send = blocked_send

    await coordinator.update(snapshot(1, "when was dune published"))
    await asyncio.wait_for(decision_started.wait(), timeout=0.1)
    coordinator._cancel_for_correction()
    await asyncio.sleep(0)

    assert coordinator.metrics.trigger_cancellations == 1
    assert coordinator.metrics.unpriced_trigger_cancellations == 0
    assert coordinator.metrics.trigger_usage.calls == 1
    release_decision.set()
    await coordinator.close()


@pytest.mark.asyncio
async def test_repeated_cancel_request_counts_one_controller_call_once() -> None:
    coordinator, _ = await make_coordinator()
    task = asyncio.create_task(asyncio.Event().wait())
    coordinator._trigger_task = task
    coordinator._trigger_call_states[task] = "pending"

    coordinator._cancel_trigger_task(task)
    coordinator._cancel_trigger_task(task)
    await asyncio.gather(task, return_exceptions=True)

    assert coordinator.metrics.trigger_cancellations == 1
    assert coordinator.metrics.unpriced_trigger_cancellations == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_repeated_retrieval_cancel_counts_once_and_close_awaits_cleanup() -> None:
    class CleanupAwareIndex:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release_cleanup = asyncio.Event()
            self.cleanup_finished = asyncio.Event()

        async def search(self, query: str, *, cache_scope: str = "default") -> SearchResult:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release_cleanup.wait()
                self.cleanup_finished.set()
                raise

    index = CleanupAwareIndex()
    coordinator, _ = await make_coordinator(index=index)
    coordinator._start_retrieval(snapshot(1, "who wrote dune"), "dune author")
    await asyncio.wait_for(index.started.wait(), timeout=0.1)
    retrieval = coordinator._retrieval_task
    assert retrieval is not None

    coordinator._cancel_for_correction()
    assert coordinator._cancel_retrieval_task(retrieval) is False
    await asyncio.wait_for(index.cancelled.wait(), timeout=0.1)

    assert coordinator.metrics.retrieval_cancellations == 1
    assert coordinator._retrieval_task is None
    closing = asyncio.create_task(coordinator.close())
    await asyncio.sleep(0)
    assert not closing.done()

    index.release_cleanup.set()
    await asyncio.wait_for(closing, timeout=0.1)
    assert index.cleanup_finished.is_set()


@pytest.mark.asyncio
async def test_commit_without_reusable_work_retrieves_exact_text_once() -> None:
    trigger = ControlledTrigger()

    index = FakeIndex()
    coordinator, events = await make_coordinator(trigger=trigger, index=index)
    committed = snapshot(1, "what year was dune published?")

    evidence = await coordinator.commit(committed)

    assert trigger.calls == []
    assert index.calls == [(committed.text, "stream:test")]
    assert evidence.source_text == committed.text
    assert evidence.query == committed.text
    assert coordinator.metrics.commit_fallbacks == 1
    assert coordinator.metrics.accepted_from_fallback is True
    assert coordinator.metrics.trigger_calls == 0
    assert coordinator.metrics.trigger_usage.calls == 0
    assert coordinator.metrics.controller_failures == 0
    assert coordinator.metrics.accepted_retrieval_started_ms is not None
    assert coordinator.metrics.accepted_retrieval_ready_ms is not None
    assert coordinator.metrics.first_retrieval_started_ms is None
    assert coordinator.metrics.first_retrieval_ready_ms is None
    assert (
        coordinator.metrics.accepted_retrieval_ready_ms
        >= coordinator.metrics.accepted_retrieval_started_ms
    )
    assert any(event["type"] == "retrieval.fallback" for event in events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_commit_rejects_validated_evidence_when_send_adds_punctuation() -> None:
    coordinator, events = await make_coordinator()
    coordinator.evidence = RetrievalEvidence(
        source_text="who is number one in the world",
        revision=10,
        query="current world number one",
        result=search_result("current world number one"),
        started_ms=10.0,
        completed_ms=20.0,
        validated_ms=30.0,
        controller_validated=True,
    )

    evidence = await coordinator.commit(snapshot(11, "who is number one in the world?"))

    assert evidence.source_text == "who is number one in the world?"
    assert coordinator.metrics.evidence_reuses == 0
    assert coordinator.metrics.commit_fallbacks == 1
    assert all(event.get("type") != "retrieval.revalidated" for event in events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_commit_stops_unfinished_speculation_before_exact_fallback() -> None:
    trigger = ControlledTrigger()
    index = FakeIndex()
    coordinator, _ = await make_coordinator(trigger=trigger, index=index)
    partial = snapshot(1, "how many jobs did amazon cut")
    await coordinator.update(partial)
    await asyncio.wait_for(trigger.started.wait(), timeout=0.1)

    committed = snapshot(2, "how many jobs did amazon cut in january 2023?")
    await coordinator.commit(committed)

    assert trigger.cancelled == 1
    assert coordinator.metrics.trigger_cancellations == 1
    assert index.calls == [(committed.text, "stream:test")]
    await coordinator.close()


@pytest.mark.asyncio
async def test_exact_commit_retrieval_failure_is_not_retried() -> None:
    class FailingIndex(FakeIndex):
        async def search(self, query: str, *, cache_scope: str = "default") -> SearchResult:
            self.calls.append((query, cache_scope))
            raise RuntimeError("exact retrieval failed")

    index = FailingIndex()
    coordinator, _ = await make_coordinator(index=index)

    with pytest.raises(RuntimeError, match="exact retrieval failed"):
        await coordinator.commit(snapshot(1, "what year was dune published?"))

    assert index.calls == [("what year was dune published?", "stream:test")]
    assert coordinator.metrics.retrieval_failures == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_completed_superseded_retrieval_embeddings_remain_billable() -> None:
    trigger = SequencedTrigger(
        TriggerDecision(action="retrieve", retrieval_query="first query"),
        TriggerDecision(action="retrieve", retrieval_query="replacement query"),
    )
    index = FakeIndex()
    coordinator, _ = await make_coordinator(trigger=trigger, index=index)

    await coordinator.update(snapshot(1, "one two three"))
    await wait_until(lambda: coordinator.evidence is not None, "first retrieval missing")
    await coordinator.update(snapshot(2, "one two three four five six"))
    await wait_until(
        lambda: (
            coordinator.evidence is not None and coordinator.evidence.query == "replacement query"
        ),
        "replacement retrieval missing",
    )

    assert index.calls == [
        ("first query", "stream:test"),
        ("replacement query", "stream:test"),
    ]
    assert coordinator.evidence.result.embedding_tokens == 1
    assert coordinator.metrics.retrieval_embedding_tokens == 2
    await coordinator.close()


@pytest.mark.asyncio
async def test_inflight_retrieval_finishing_after_send_is_not_precommit_ready() -> None:
    query = "dune publication year"
    trigger = SequencedTrigger(TriggerDecision(action="retrieve", retrieval_query=query))

    index = FakeIndex()
    index.release.clear()
    coordinator, events = await make_coordinator(trigger=trigger, index=index)
    complete = snapshot(1, "when was dune published?")

    await coordinator.update(complete)
    await wait_until(lambda: bool(index.calls), "retrieval did not start")
    commit_task = asyncio.create_task(coordinator.commit(complete))
    await asyncio.sleep(0)
    index.release.set()
    await commit_task

    assert coordinator.metrics.accepted_from_fallback is False
    assert coordinator.metrics.accepted_ready_before_commit is False
    reused = [event for event in events if event["type"] == "retrieval.reused"]
    assert reused[-1]["ready_before_commit"] is False
    await coordinator.close()


@pytest.mark.asyncio
async def test_timeout_and_direct_fallback_metrics_are_visible() -> None:
    class SlowTrigger(ControlledTrigger):
        async def decide(self, **kwargs) -> TriggerResult:
            del kwargs
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

    trigger = SlowTrigger()
    index = FakeIndex()
    coordinator, events = await make_coordinator(
        trigger=trigger,
        index=index,
        trigger_timeout_s=0.01,
    )

    await coordinator.update(snapshot(1, "what year was dune published"))
    await asyncio.sleep(0.03)
    evidence = await coordinator.commit(snapshot(2, "what year was dune published?"))

    assert coordinator.metrics.trigger_timeouts == 1
    assert coordinator.metrics.commit_fallbacks == 1
    assert coordinator.metrics.retrieval_calls == 1
    assert evidence.result.cache_scope == "stream:test"
    assert index.calls == [("what year was dune published?", "stream:test")]
    assert any(
        event["type"] == "trigger.error" and event["reason"] == "timeout" for event in events
    )
    await coordinator.close()


@pytest.mark.asyncio
async def test_failed_retrieval_clears_nonexistent_previous_query() -> None:
    class FailingIndex(FakeIndex):
        async def search(self, query: str, *, cache_scope: str = "default") -> SearchResult:
            self.calls.append((query, cache_scope))
            raise RuntimeError("index unavailable")

    trigger = SequencedTrigger(TriggerDecision(action="retrieve", retrieval_query="failed query"))
    coordinator, events = await make_coordinator(
        trigger=trigger,
        index=FailingIndex(),
    )

    await coordinator.update(snapshot(1, "what year was dune published"))
    await wait_until(
        lambda: any(event["type"] == "retrieval.error" for event in events),
        "retrieval failure event missing",
    )

    assert coordinator.previous_query is None
    assert coordinator.evidence is None
    assert coordinator.metrics.retrieval_failures == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_keep_previous_promotes_ready_prefix_evidence_for_exact_commit() -> None:
    query = "dune publication year"
    trigger = SequencedTrigger(
        TriggerDecision(action="retrieve", retrieval_query=query),
        TriggerDecision(action="keep_previous"),
    )
    coordinator, events = await make_coordinator(trigger=trigger)
    prefix = snapshot(1, "when was dune")
    complete = snapshot(2, "when was dune published?")

    await coordinator.update(prefix)
    await wait_until(
        lambda: coordinator.evidence is not None,
        "prefix retrieval did not complete",
    )
    prefix_ready_ms = coordinator.evidence.completed_ms
    await coordinator.update(complete)
    await wait_until(
        lambda: (
            coordinator.evidence is not None and coordinator.evidence.source_text == complete.text
        ),
        "keep_previous did not promote ready evidence",
    )

    evidence = await coordinator.commit(complete)

    assert evidence.source_text == complete.text
    assert evidence.revision == complete.revision
    assert evidence.query == query
    assert evidence.completed_ms == prefix_ready_ms
    assert evidence.validated_ms is not None
    assert evidence.validated_ms >= prefix_ready_ms
    assert coordinator.metrics.evidence_revalidations == 1
    assert coordinator.metrics.evidence_reuses == 1
    assert coordinator.metrics.commit_fallbacks == 0
    assert coordinator.metrics.accepted_retrieval_ready_ms == evidence.validated_ms
    assert any(
        event["type"] == "retrieval.revalidated" and event["state"] == "ready" for event in events
    )
    assert trigger.calls == [(prefix.text, None), (complete.text, query)]
    await coordinator.close()


@pytest.mark.asyncio
async def test_keep_previous_promotes_matching_inflight_retrieval_when_ready() -> None:
    query = "dune publication year"
    trigger = SequencedTrigger(
        TriggerDecision(action="retrieve", retrieval_query=query),
        TriggerDecision(action="keep_previous"),
    )
    index = FakeIndex()
    index.release.clear()

    coordinator, events = await make_coordinator(
        trigger=trigger,
        index=index,
    )
    prefix = snapshot(1, "when was dune")
    complete = snapshot(2, "when was dune published?")

    await coordinator.update(prefix)
    await wait_until(lambda: bool(index.calls), "prefix retrieval did not start")
    await coordinator.update(complete)
    await wait_until(
        lambda: coordinator.metrics.evidence_revalidations == 1,
        "keep_previous did not mark in-flight retrieval",
    )
    assert coordinator.evidence is None
    index.release.set()
    await wait_until(
        lambda: coordinator.evidence is not None,
        "promoted in-flight retrieval did not complete",
    )

    evidence = await coordinator.commit(complete)

    assert evidence.source_text == complete.text
    assert evidence.revision == complete.revision
    assert evidence.query == query
    assert evidence.validated_ms == evidence.completed_ms
    assert coordinator.metrics.evidence_reuses == 1
    assert coordinator.metrics.commit_fallbacks == 0
    assert any(
        event["type"] == "retrieval.revalidated" and event["state"] == "in_flight"
        for event in events
    )
    await coordinator.close()
