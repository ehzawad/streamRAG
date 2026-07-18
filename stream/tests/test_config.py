from dataclasses import replace

import pytest

from stream.config import StreamSettings


@pytest.mark.parametrize("delay_ms", [0, 700])
def test_stream_settings_reject_unlocked_settled_draft_delay(delay_ms: int) -> None:
    with pytest.raises(ValueError, match="500 ms draft settling"):
        replace(StreamSettings(), settled_draft_delay_ms=delay_ms).validate()


def test_stream_settings_reject_unlocked_trigger_reasoning() -> None:
    with pytest.raises(ValueError, match="streaming trigger"):
        replace(StreamSettings(), trigger_reasoning_effort="medium").validate()
