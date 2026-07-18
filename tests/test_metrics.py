from __future__ import annotations

import pytest

from app.config import Settings
from app.metrics import model_cost
from app.models import Usage


def test_model_cost_accounts_for_cache_writes_reads_and_uncached_input() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        cache_write_tokens=200_000,
        cached_input_tokens=300_000,
        output_tokens=100_000,
    )

    cost = model_cost(usage, Settings())

    assert cost.input_usd == pytest.approx(2.5)
    assert cost.cache_write_usd == pytest.approx(1.25)
    assert cost.cached_input_usd == pytest.approx(0.15)
    assert cost.output_usd == pytest.approx(3.0)
    assert cost.total_usd == pytest.approx(6.9)
