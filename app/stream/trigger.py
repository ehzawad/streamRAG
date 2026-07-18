from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

from app.agent.openai_client import responses_model
from app.agent.summary_skill import pydantic_usage
from app.config import Settings
from app.models import TriggerDecision, Usage


@dataclass(frozen=True)
class TriggerResult:
    decision: TriggerDecision
    usage: Usage
    elapsed_ms: float


TRIGGER_POLICY = """You control speculative retrieval for a typed streaming RAG system.
Input is a cumulative draft while the user is typing.

Choose exactly one action:
- wait: intent/entities are still too incomplete or ambiguous for a precise search.
- retrieve: enough stable intent exists; emit a short standalone factual retrieval query.
- keep_previous: the previous successful query still covers this draft.

Precision rules:
- Prefer wait over an ambiguous or premature query.
- Never invent missing entities, dates, constraints, or user intent.
- Retrieve once the object/entity and requested property or relation are clear.
- For comparisons, wait until every compared target needed for retrieval is present.
- Treat output-format or explanation instructions as non-retrieval-changing when
  the existing query already targets the same answer-bearing evidence.
- If the draft materially corrects the entity or intent, retrieve a corrected query.
- At commit, do not wait for a complete factual question; retrieve if corpus evidence could help.
"""


class ModelTrigger:
    def __init__(self, settings: Settings):
        self.settings = settings
        model, self._client = responses_model(
            settings,
            timeout_s=settings.trigger_timeout_s,
        )
        model_settings = OpenAIResponsesModelSettings(
            openai_reasoning_effort=settings.trigger_reasoning_effort,
            openai_reasoning_mode="standard",
            openai_service_tier=settings.openai_service_tier,
            openai_store=False,
            openai_text_verbosity="low",
            max_tokens=120,
        )
        self.agent = Agent(
            model,
            name="streamrag_trigger",
            output_type=TriggerDecision,
            instructions=TRIGGER_POLICY,
            model_settings=model_settings,
        )

    async def close(self) -> None:
        await self._client.close()

    async def decide(
        self,
        *,
        draft: str,
        previous_query: str | None,
        conversation_context: str,
        is_commit: bool,
    ) -> TriggerResult:
        started = time.perf_counter()
        result = await self.agent.run(
            "Cumulative draft:\n"
            f"{draft}\n\nPrevious successful query: {previous_query or '(none)'}\n"
            f"Recent conversation (may resolve follow-up references):\n"
            f"{conversation_context or '(none)'}\n"
            f"Input committed: {is_commit}",
            usage_limits=UsageLimits(request_limit=1, output_tokens_limit=120),
        )
        decision = result.output
        # A commit cannot be stranded by a wait decision when no usable evidence exists.
        if is_commit and decision.action == "wait":
            decision = TriggerDecision(action="retrieve", retrieval_query=draft)
        return TriggerResult(
            decision=decision,
            usage=pydantic_usage(result.usage, "trigger"),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
