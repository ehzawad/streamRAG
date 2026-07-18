from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.agent.summary_skill import (
    ConversationSummarySkill,
    append_conversation_turn,
    message_text,
)
from app.config import Settings
from app.data.session_store import SessionMemory, SessionStore


@dataclass
class FakeRawUsage:
    input_tokens: int = 30
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 8
    requests: int = 1


@dataclass
class FakeSummaryResult:
    output: str = "The user is tracking Project Atlas and requires concise answers."
    usage: FakeRawUsage = field(default_factory=FakeRawUsage)


class FakeSummaryAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, prompt: str, **_kwargs) -> FakeSummaryResult:
        self.prompts.append(prompt)
        return FakeSummaryResult()


@pytest.mark.asyncio
async def test_long_history_compacts_and_persists_without_tool_evidence(tmp_path) -> None:
    settings = Settings(
        runtime_db=tmp_path / "runtime.sqlite3",
        history_token_budget=40,
        history_keep_turns=1,
    )
    memory = SessionMemory(summary="Existing constraint: use metric units.")
    append_conversation_turn(memory, "Project Atlas goal " * 20, "First answer " * 20)
    append_conversation_turn(memory, "Second user turn " * 20, "Second answer " * 20)
    append_conversation_turn(memory, "Keep this recent question", "Keep this recent answer")

    skill = ConversationSummarySkill(settings)
    fake_agent = FakeSummaryAgent()
    skill.agent = fake_agent  # type: ignore[assignment]
    result = await skill.compact(memory)

    assert result.compressed is True
    assert result.memory.summary.startswith("The user is tracking Project Atlas")
    assert result.memory.compression_calls == 1
    assert [message_text(message) for message in result.memory.messages] == [
        "Keep this recent question",
        "Keep this recent answer",
    ]
    assert result.usage.calls == 1
    assert "Existing constraint: use metric units." in fake_agent.prompts[0]
    assert "Project Atlas goal" in fake_agent.prompts[0]
    assert "Keep this recent question" not in fake_agent.prompts[0]

    store = SessionStore(settings.runtime_db)
    await store.setup()
    await store.save("session:stream", result.memory)
    restored = await store.load("session:stream")

    assert restored.summary == result.memory.summary
    assert restored.compression_calls == 1
    assert [message_text(message) for message in restored.messages] == [
        "Keep this recent question",
        "Keep this recent answer",
    ]


@pytest.mark.asyncio
async def test_summary_timeout_preserves_full_memory() -> None:
    class StalledSummaryAgent:
        async def run(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    settings = Settings(
        history_token_budget=1,
        history_keep_turns=1,
        summary_timeout_s=0.01,
    )
    memory = SessionMemory()
    append_conversation_turn(memory, "old question " * 10, "old answer " * 10)
    append_conversation_turn(memory, "new question", "new answer")
    skill = ConversationSummarySkill(settings)
    skill.agent = StalledSummaryAgent()  # type: ignore[assignment]

    result = await skill.compact(memory)

    assert result.compressed is False
    assert result.memory is memory


@pytest.mark.asyncio
async def test_session_lock_entry_is_released_after_lease(tmp_path) -> None:
    store = SessionStore(tmp_path / "runtime.sqlite3")
    await store.setup()

    async with store.lease("session"):
        assert "session" in store._locks

    assert "session" not in store._locks
