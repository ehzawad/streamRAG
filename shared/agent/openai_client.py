from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from shared.config import Settings


def responses_model(
    settings: Settings,
    *,
    timeout_s: float,
) -> tuple[OpenAIResponsesModel, AsyncOpenAI]:
    client = AsyncOpenAI(
        timeout=timeout_s,
        # Application-owned hard deadlines make hidden SDK retries impossible to
        # attribute precisely, so each measured call is one transport attempt.
        max_retries=0,
    )
    model = OpenAIResponsesModel(
        settings.openai_model,
        provider=OpenAIProvider(openai_client=client),
    )
    return model, client
