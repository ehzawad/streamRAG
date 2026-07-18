from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import Usage


@dataclass(frozen=True)
class CostBreakdown:
    input_usd: float
    cache_write_usd: float
    cached_input_usd: float
    output_usd: float
    total_usd: float


def model_cost(usage: Usage, settings: Settings) -> CostBreakdown:
    uncached = max(
        0,
        usage.input_tokens - usage.cache_write_tokens - usage.cached_input_tokens,
    )
    input_usd = uncached * settings.sol_input_per_million / 1_000_000
    cache_write_usd = (
        usage.cache_write_tokens * settings.sol_cache_write_per_million / 1_000_000
    )
    cached_usd = usage.cached_input_tokens * settings.sol_cached_input_per_million / 1_000_000
    output_usd = usage.output_tokens * settings.sol_output_per_million / 1_000_000
    return CostBreakdown(
        input_usd=input_usd,
        cache_write_usd=cache_write_usd,
        cached_input_usd=cached_usd,
        output_usd=output_usd,
        total_usd=input_usd + cache_write_usd + cached_usd + output_usd,
    )


class JsonlMetricLogger:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    async def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def usage_record(usage: Usage, settings: Settings) -> dict[str, Any]:
    return {"usage": asdict(usage), "estimated_cost_usd": asdict(model_cost(usage, settings))}
