from types import SimpleNamespace

import pytest

from app.models import TriggerDecision
from app.stream.coordinator import has_terminal_boundary, meaningful_completed_prefix
from app.stream.trigger import ModelTrigger


def test_terminal_punctuation_can_recheck_after_one_new_word() -> None:
    assert has_terminal_boundary("when was the great depression?", "when was the great")


def test_unchanged_or_incomplete_text_is_not_a_new_boundary() -> None:
    assert not has_terminal_boundary("when was the great", "when was")
    assert not has_terminal_boundary("when was the great?", "when was the great?")


def test_raw_candidate_uses_completed_tokens_only() -> None:
    assert meaningful_completed_prefix(
        "Who wrote Dune ", minimum_words=3
    ) == "Who wrote Dune"
    assert meaningful_completed_prefix(
        "Who wrote The Great Gats", minimum_words=3
    ) == "Who wrote The Great"


def test_raw_candidate_requires_a_request_and_two_content_words() -> None:
    assert meaningful_completed_prefix("what is the name of ", minimum_words=3) is None
    assert (
        meaningful_completed_prefix(
            "five ordinary words without a request ", minimum_words=3
        )
        is None
    )


@pytest.mark.asyncio
async def test_commit_wait_fallback_bounds_oversized_draft() -> None:
    class WaitAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                output=TriggerDecision(action="wait"),
                usage=SimpleNamespace(requests=1),
            )

    trigger = object.__new__(ModelTrigger)
    trigger.agent = WaitAgent()
    draft = f"start marker {'x' * 19_976} end marker"
    assert len(draft) == 20_000

    result = await trigger.decide(
        draft=draft,
        previous_query=None,
        conversation_context="",
        is_commit=True,
    )

    query = result.decision.retrieval_query
    assert result.decision.action == "retrieve"
    assert query is not None
    assert len(query) == 2_000
    assert query.startswith("start marker")
    assert query.endswith("end marker")
