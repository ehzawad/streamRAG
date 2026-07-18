from __future__ import annotations

import pytest

from app.api.events import EventChannel, EventRegistry


@pytest.mark.asyncio
async def test_event_channel_replays_events_published_before_subscribe() -> None:
    channel = EventChannel()
    await channel.publish({"type": "one"})
    await channel.publish({"type": "two"})
    await channel.close()
    events = [event async for event in channel.subscribe()]
    assert [event["event"] for event in events] == ["one", "two"]
    assert [event["id"] for event in events] == ["1", "2"]


@pytest.mark.asyncio
async def test_registry_releases_closed_channels() -> None:
    registry = EventRegistry()
    channel = await registry.get("turn:one")
    await channel.publish({"type": "one"})
    assert await registry.size() == 1

    await registry.close_and_remove("turn:one")

    assert channel.closed is True
    assert await registry.size() == 0
