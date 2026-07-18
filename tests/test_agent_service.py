from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic_ai import AgentRunResult, AgentRunResultEvent

from app.agent.openai_client import responses_model
from app.agent.service import AgentDeps, GroundedAgent, execute_local_crag_search
from app.agent.summary_skill import (
    CompressionResult,
    append_conversation_turn,
    message_text,
)
from app.config import Settings
from app.data.session_store import SessionStore
from app.models import Usage


class CancelledSearchStore:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, *_args, **_kwargs):
        self.calls += 1
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_timed_out_tool_cannot_touch_store_on_retry() -> None:
    store = CancelledSearchStore()
    traces: list[dict] = []
    deps = AgentDeps(
        store=store,  # type: ignore[arg-type]
        evidence="",
        query_time="",
        memory_summary="",
        context_token_budget=100,
        cache_scope="test:stream",
        tool_traces=traces,
    )

    with pytest.raises(asyncio.CancelledError):
        await execute_local_crag_search(deps, "first query")
    with pytest.raises(RuntimeError, match="at most once"):
        await execute_local_crag_search(deps, "retry query")

    assert store.calls == 1
    assert deps.tool_attempts == 2
    assert [trace["status"] for trace in traces] == [
        "timeout_or_cancelled",
        "rejected_attempt_limit",
    ]
    assert traces[0]["accounting_complete"] is False
    assert traces[1]["accounting_complete"] is True


def test_local_search_uses_openai_strict_function_schema() -> None:
    agent = GroundedAgent(Settings(), object(), object())  # type: ignore[arg-type]
    tool = agent.agent._function_toolset.tools["search_local_crag"]

    assert tool.strict is True
    assert tool.function_schema.json_schema["additionalProperties"] is False
    assert tool.function_schema.json_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_generation_boundary_keeps_session_lease_through_persistence(tmp_path) -> None:
    class FakePydanticAgent:
        def __init__(self) -> None:
            self.calls = 0

        def run_stream_events(self, question: str, **_kwargs):
            @asynccontextmanager
            async def context():
                self.calls += 1

                async def events():
                    yield AgentRunResultEvent(AgentRunResult(f"answer to {question}"))

                yield events()

            return context()

    class BlockingSummary:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def compact(self, memory):
            self.started.set()
            await self.release.wait()
            return CompressionResult(memory=memory, usage=Usage(), compressed=False)

    settings = Settings(runtime_db=tmp_path / "runtime.sqlite3")
    sessions = SessionStore(settings.runtime_db)
    await sessions.setup()
    fake_agent = FakePydanticAgent()
    summary = BlockingSummary()
    subject = object.__new__(GroundedAgent)
    subject.settings = settings
    subject.store = object()
    subject.sessions = sessions
    subject.agent = fake_agent
    subject.summary_skill = summary

    first = subject.stream(
        session_key="shared:naive",
        question="first question",
        evidence="evidence",
        query_time="",
        cache_scope="shared:naive",
    )
    delta = await anext(first)
    assert delta == {"type": "answer.delta", "text": "answer to first question"}
    generated = await anext(first)
    assert generated["type"] == "agent.completed"
    assert not summary.started.is_set()

    persisting = asyncio.create_task(anext(first))
    await summary.started.wait()
    second = subject.stream(
        session_key="shared:naive",
        question="second question",
        evidence="evidence",
        query_time="",
        cache_scope="shared:naive",
    )
    second_generated = asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    assert fake_agent.calls == 1
    assert not second_generated.done()

    summary.release.set()
    persisted = await persisting
    assert persisted["type"] == "agent.persisted"
    with pytest.raises(StopAsyncIteration):
        await anext(first)
    generated_second = await asyncio.wait_for(second_generated, timeout=0.1)
    assert generated_second["type"] == "answer.delta"
    generated_second = await anext(second)
    assert generated_second["type"] == "agent.completed"
    assert fake_agent.calls == 2
    await second.aclose()

    memory = await sessions.load("shared:naive")
    assert [message_text(message) for message in memory.messages] == [
        "first question",
        "answer to first question",
    ]


@pytest.mark.asyncio
async def test_conversation_context_waits_for_pending_session_save(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "runtime.sqlite3")
    await sessions.setup()
    subject = object.__new__(GroundedAgent)
    subject.sessions = sessions

    async with sessions.lease("shared:stream"):
        memory = await sessions.load("shared:stream")
        append_conversation_turn(memory, "Who wrote Dune?", "Frank Herbert wrote Dune.")
        context_task = asyncio.create_task(subject.conversation_context("shared:stream"))
        await asyncio.sleep(0)
        assert not context_task.done()
        await sessions.save("shared:stream", memory)

    context = await asyncio.wait_for(context_task, timeout=0.1)
    assert "Who wrote Dune?" in context
    assert "Frank Herbert wrote Dune." in context


@pytest.mark.asyncio
async def test_compaction_failure_still_persists_completed_turn(tmp_path) -> None:
    class FakePydanticAgent:
        def run_stream_events(self, question: str, **_kwargs):
            @asynccontextmanager
            async def context():
                async def events():
                    yield AgentRunResultEvent(AgentRunResult(f"answer to {question}"))

                yield events()

            return context()

    class FailingSummary:
        async def compact(self, _memory):
            raise RuntimeError("summary provider unavailable")

    settings = Settings(runtime_db=tmp_path / "runtime.sqlite3")
    sessions = SessionStore(settings.runtime_db)
    await sessions.setup()
    subject = object.__new__(GroundedAgent)
    subject.settings = settings
    subject.store = object()
    subject.sessions = sessions
    subject.agent = FakePydanticAgent()
    subject.summary_skill = FailingSummary()

    stream = subject.stream(
        session_key="shared:naive",
        question="completed question",
        evidence="evidence",
        query_time="",
        cache_scope="shared:naive",
    )
    assert (await anext(stream))["type"] == "answer.delta"
    assert (await anext(stream))["type"] == "agent.completed"
    with pytest.raises(RuntimeError, match="summary provider unavailable"):
        await anext(stream)

    memory = await sessions.load("shared:naive")
    assert [message_text(message) for message in memory.messages] == [
        "completed question",
        "answer to completed question",
    ]


@pytest.mark.asyncio
async def test_responses_client_disables_sdk_retries_under_app_deadlines() -> None:
    _, client = responses_model(Settings(), timeout_s=1.0)
    try:
        assert client.max_retries == 0
    finally:
        await client.close()


def test_persistence_deadline_must_outlive_summary_deadline() -> None:
    settings = Settings(
        summary_timeout_s=8.0,
        post_answer_persistence_timeout_s=8.0,
    )

    with pytest.raises(
        ValueError,
        match="POST_ANSWER_PERSISTENCE_TIMEOUT_S must exceed SUMMARY_TIMEOUT_S",
    ):
        settings.validate()
