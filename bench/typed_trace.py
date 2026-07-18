from __future__ import annotations

import math
from dataclasses import dataclass

SNAPSHOT_INTERVAL_MS = 400
STANDARD_WORD_CHARACTERS = 5


@dataclass(frozen=True)
class TypedSnapshot:
    planned_offset_ms: float
    text: str
    character_count: int
    word_count: int
    is_final: bool


def typing_duration_ms(text: str, words_per_minute: float) -> float:
    """Approximate typing with the conventional five-character WPM definition."""

    normalized = text.strip()
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be positive")
    if len(normalized) <= 1:
        return 0.0
    character_interval_ms = 60_000 / (words_per_minute * STANDARD_WORD_CHARACTERS)
    return (len(normalized) - 1) * character_interval_ms


def cumulative_typed_trace(
    text: str,
    words_per_minute: float,
    snapshot_interval_ms: int = SNAPSHOT_INTERVAL_MS,
) -> list[TypedSnapshot]:
    """Create cumulative dirty-text samples strictly before immediate Send.

    Characters arrive uniformly under the standard five-character WPM convention.
    A user who presses Send as soon as typing finishes cancels the next UI timer, so
    the full query exists only in the commit payload unless it was sampled earlier.
    This function deliberately does not manufacture an exact-query snapshot at Send.
    """

    normalized = text.strip()
    if not normalized:
        raise ValueError("typed trace text is empty")
    if snapshot_interval_ms <= 0:
        raise ValueError("snapshot_interval_ms must be positive")
    character_interval_ms = 60_000 / (words_per_minute * STANDARD_WORD_CHARACTERS)
    duration_ms = typing_duration_ms(normalized, words_per_minute)
    snapshots: list[TypedSnapshot] = []
    planned_ms = float(snapshot_interval_ms)
    last_text = ""
    while planned_ms < duration_ms:
        character_count = min(
            len(normalized),
            max(1, math.floor(planned_ms / character_interval_ms) + 1),
        )
        prefix = normalized[:character_count].strip()
        if prefix and prefix != last_text:
            snapshots.append(
                TypedSnapshot(
                    planned_offset_ms=planned_ms,
                    text=prefix,
                    character_count=character_count,
                    word_count=len(prefix.split()),
                    is_final=False,
                )
            )
            last_text = prefix
        planned_ms += snapshot_interval_ms
    return snapshots
