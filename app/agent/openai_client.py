from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import Settings


def responses_model(
    settings: Settings,
    *,
    timeout_s: float,
) -> tuple[OpenAIResponsesModel, AsyncOpenAI]:
    client = AsyncOpenAI(
        timeout=timeout_s,
        # The application owns a hard deadline and role-specific fallback for
        # every model call. SDK retries would otherwise outlive or be cancelled
        # by that outer budget, so only embeddings retain transport retries.
        max_retries=0,
    )
    model = OpenAIResponsesModel(
        settings.openai_model,
        provider=OpenAIProvider(openai_client=client),
    )
    return model, client
