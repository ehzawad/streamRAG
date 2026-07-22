from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.config import ROOT, Settings


@dataclass(frozen=True)
class StreamSettings(Settings):
    # Fixed StreamRAG scheduling contract; not runtime-configurable.
    trigger_reasoning_effort: str = "low"
    trigger_min_tokens: int = 5
    trigger_min_new_tokens: int = 3
    trigger_interval_ms: int = 500
    trigger_max_presubmit_calls: int = 4
    parallel_raw_retrieval: bool = True
    settled_draft_delay_ms: int = 500

    trigger_timeout_s: float = float(os.getenv("TRIGGER_TIMEOUT_S", "4.0"))

    def validate(self) -> None:
        super().validate()
        if self.trigger_timeout_s <= 0:
            raise ValueError("TRIGGER_TIMEOUT_S must be positive")

    def public_metadata(self) -> dict[str, object]:
        return {
            "trigger_reasoning_effort": self.trigger_reasoning_effort,
            "trigger_min_tokens": self.trigger_min_tokens,
            "trigger_min_new_tokens": self.trigger_min_new_tokens,
            "trigger_interval_ms": self.trigger_interval_ms,
            "trigger_max_presubmit_calls": self.trigger_max_presubmit_calls,
            "parallel_raw_retrieval": self.parallel_raw_retrieval,
            "settled_draft_delay_ms": self.settled_draft_delay_ms,
            "trigger_timeout_s": self.trigger_timeout_s,
        }


settings = StreamSettings(
    qdrant_path=Path(os.getenv("QDRANT_PATH", ROOT / "var" / "stream" / "qdrant")),
    runtime_db=Path(os.getenv("RUNTIME_DB", ROOT / "var" / "stream" / "runtime.sqlite3")),
    metrics_log=Path(os.getenv("METRICS_LOG", ROOT / "var" / "stream" / "requests.jsonl")),
)
settings.validate()
