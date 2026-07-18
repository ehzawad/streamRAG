from __future__ import annotations

import asyncio

import pytest

from app.agent.service import AgentDeps, GroundedAgent, execute_local_crag_search
from app.config import Settings


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
