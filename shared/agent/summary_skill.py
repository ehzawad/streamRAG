from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from pydantic_ai import Agent, ModelMessage, UsageLimits
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

from shared.agent.context import token_count
from shared.agent.openai_client import responses_model
from shared.config import Settings
from shared.data.session_store import SessionMemory
from shared.models import Usage


def message_text(message: ModelMessage) -> str:
    pieces: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            pieces.append(str(part.content))
        elif isinstance(part, TextPart):
            pieces.append(part.content)
    return " ".join(pieces)


def pydantic_usage(raw: object, name: str) -> Usage:
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(raw, "cache_write_tokens", 0) or 0),
        cached_input_tokens=int(getattr(raw, "cache_read_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
        calls=int(getattr(raw, "requests", 0) or 0),
        names=[name],
    )


@dataclass(frozen=True)
class CompressionResult:
    memory: SessionMemory
    usage: Usage
    compressed: bool
    accounting_complete: bool = True
    unpriced_timeout_calls: int = 0
    stats: dict | None = None


class ConversationSummarySkill:
    """Explicit reusable skill invoked only when durable history exceeds budget."""

    def __init__(self, settings: Settings):
        model, self._client = responses_model(
            settings,
            timeout_s=settings.summary_timeout_s,
        )
        model_settings = OpenAIResponsesModelSettings(
            openai_reasoning_effort=settings.summary_reasoning_effort,
            openai_service_tier=settings.openai_service_tier,
            openai_store=False,
            openai_text_verbosity="low",
            max_tokens=320,
        )
        self.agent = Agent(
            model,
            name="conversation_summary_skill",
            instructions=(
                "Compress conversation memory into at most 220 words. Preserve user goals, "
                "constraints, decisions, unresolved questions, and named entities. Never preserve "
                "retrieved document passages, citations, or tool output. Return only the summary."
            ),
            model_settings=model_settings,
        )
        self.settings = settings

    async def close(self) -> None:
        await self._client.close()

    async def compact(self, memory: SessionMemory) -> CompressionResult:
        rendered = "\n".join(message_text(message) for message in memory.messages)
        message_tokens_before = token_count(rendered)
        summary_tokens_before = token_count(memory.summary) if memory.summary else 0
        base_stats = {
            "message_count_before": len(memory.messages),
            "message_tokens_before": message_tokens_before,
            "summary_tokens_before": summary_tokens_before,
            "context_tokens_before": message_tokens_before + summary_tokens_before,
            "history_token_budget": self.settings.history_token_budget,
            "history_keep_turns": self.settings.history_keep_turns,
            "compression_calls": memory.compression_calls,
        }
        if message_tokens_before <= self.settings.history_token_budget:
            return CompressionResult(
                memory,
                Usage(),
                False,
                stats={**base_stats, "status": "not_needed", "reason": "within_token_budget"},
            )
        keep_count = self.settings.history_keep_turns * 2
        if len(memory.messages) <= keep_count:
            return CompressionResult(
                memory,
                Usage(),
                False,
                stats={
                    **base_stats,
                    "status": "not_needed",
                    "reason": "insufficient_message_count",
                },
            )
        old_messages = memory.messages[:-keep_count]
        recent_messages = memory.messages[-keep_count:]
        transcript = "\n".join(
            f"{type(message).__name__}: {message_text(message)}"
            for message in old_messages
            if message_text(message)
        )
        prompt = f"Previous summary:\n{memory.summary or '(none)'}\n\nOlder turns:\n{transcript}"
        summary_started = time.perf_counter()
        try:
            async with asyncio.timeout(self.settings.summary_timeout_s):
                result = await self.agent.run(
                    prompt,
                    usage_limits=UsageLimits(request_limit=1, output_tokens_limit=320),
                )
        except TimeoutError:
            return CompressionResult(
                memory,
                Usage(),
                False,
                accounting_complete=False,
                unpriced_timeout_calls=1,
                stats={
                    **base_stats,
                    "status": "summary_timeout",
                    "reason": "history_token_budget_exceeded",
                    "summary_elapsed_ms": (time.perf_counter() - summary_started) * 1000,
                },
            )
        summary_after = str(result.output).strip()
        compacted = SessionMemory(
            messages=recent_messages,
            summary=summary_after,
            compression_calls=memory.compression_calls + 1,
        )
        message_tokens_after = token_count(
            "\n".join(message_text(message) for message in recent_messages)
        )
        summary_tokens_after = token_count(summary_after)
        return CompressionResult(
            memory=compacted,
            usage=pydantic_usage(result.usage, "summary_skill"),
            compressed=True,
            stats={
                **base_stats,
                "status": "compressed",
                "reason": "history_token_budget_exceeded",
                "message_count_after": len(recent_messages),
                "messages_compacted": len(old_messages),
                "message_tokens_after": message_tokens_after,
                "summary_tokens_after": summary_tokens_after,
                "context_tokens_after": message_tokens_after + summary_tokens_after,
                "summary_chars_after": len(summary_after),
                "summary_words_after": len(summary_after.split()),
                "summary_elapsed_ms": (time.perf_counter() - summary_started) * 1000,
                "compression_calls": memory.compression_calls + 1,
            },
        )


def append_conversation_turn(memory: SessionMemory, question: str, answer: str) -> None:
    # Only conversational text becomes durable memory. Retrieval/tool evidence is turn-local.
    memory.messages.extend(
        [
            ModelRequest(parts=[UserPromptPart(content=question)]),
            ModelResponse(parts=[TextPart(content=answer)]),
        ]
    )
