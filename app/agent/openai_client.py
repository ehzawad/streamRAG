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
        max_retries=settings.openai_model_max_retries,
    )
    model = OpenAIResponsesModel(
        settings.openai_model,
        provider=OpenAIProvider(openai_client=client),
    )
    return model, client
