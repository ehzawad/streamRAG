from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    dataset_dir: Path = Path(os.getenv("DATASET_DIR", ROOT / "data" / "crag_eval"))
    qdrant_path: Path = Path(os.getenv("QDRANT_PATH", ROOT / "data" / "qdrant"))
    runtime_db: Path = Path(os.getenv("RUNTIME_DB", ROOT / "data" / "runtime.sqlite3"))
    metrics_log: Path = Path(os.getenv("METRICS_LOG", ROOT / "var" / "requests.jsonl"))

    # Fixed assessment contract. These define the comparison itself and are not
    # runtime-configurable; both paths must share them byte-for-byte.
    openai_model: str = "gpt-5.6-sol"
    embedding_model: str = "text-embedding-3-large"
    reasoning_effort: str = "medium"
    summary_reasoning_effort: str = "low"
    openai_service_tier: str = "default"
    qdrant_collection: str = "crag_chunks"
    embedding_dimensions: int = 3072
    openai_embedding_max_retries: int = 0
    chunk_tokens: int = 400
    chunk_overlap: int = 50
    retrieve_candidates: int = 8
    top_k: int = 5
    context_token_budget: int = 2600
    history_token_budget: int = 2200
    history_keep_turns: int = 4

    # Operational knobs.
    qdrant_url: str | None = os.getenv("QDRANT_URL") or None
    qdrant_api_key: str | None = os.getenv("QDRANT_API_KEY") or None
    openai_embedding_timeout_s: float = float(os.getenv("OPENAI_EMBEDDING_TIMEOUT_S", "45"))
    retrieval_timeout_s: float = float(os.getenv("RETRIEVAL_TIMEOUT_S", "6.0"))
    answer_timeout_s: float = float(os.getenv("ANSWER_TIMEOUT_S", "30.0"))
    summary_timeout_s: float = float(os.getenv("SUMMARY_TIMEOUT_S", "8.0"))
    post_answer_persistence_timeout_s: float = float(
        os.getenv("POST_ANSWER_PERSISTENCE_TIMEOUT_S", "10.0")
    )
    turn_idle_timeout_s: float = float(os.getenv("TURN_IDLE_TIMEOUT_S", "120"))
    session_retention_hours: float = float(os.getenv("SESSION_RETENTION_HOURS", "24"))
    query_cache_size: int = int(os.getenv("QUERY_CACHE_SIZE", "512"))
    search_cache_size: int = int(os.getenv("SEARCH_CACHE_SIZE", "256"))
    search_cache_ttl_s: float = float(os.getenv("SEARCH_CACHE_TTL_S", "120"))
    cors_origins: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if item.strip()
    )

    # Standard API prices checked 2026-07-17. Returned usage is logged; explicit
    # unpriced counters identify interrupted calls whose final usage is unavailable.
    sol_input_per_million: float = 5.0
    sol_cache_write_per_million: float = 6.25
    sol_cached_input_per_million: float = 0.5
    sol_output_per_million: float = 30.0
    embedding_input_per_million: float = 0.13

    def validate(self) -> None:
        if self.openai_embedding_timeout_s <= 0:
            raise ValueError("OPENAI_EMBEDDING_TIMEOUT_S must be positive")
        if (
            min(
                self.retrieval_timeout_s,
                self.answer_timeout_s,
                self.summary_timeout_s,
                self.post_answer_persistence_timeout_s,
                self.turn_idle_timeout_s,
                self.session_retention_hours,
            )
            <= 0
        ):
            raise ValueError("runtime deadlines and retention windows must be positive")
        if self.post_answer_persistence_timeout_s <= self.summary_timeout_s:
            raise ValueError("POST_ANSWER_PERSISTENCE_TIMEOUT_S must exceed SUMMARY_TIMEOUT_S")


settings = Settings()
settings.validate()
