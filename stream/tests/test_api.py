import stream.api as stream_api
from stream.config import StreamSettings


class TriggerStub:
    async def close(self) -> None:
        return None


def test_stream_service_exposes_typed_snapshot_surface() -> None:
    paths = stream_api.app.openapi()["paths"]
    assert "application/json" in paths["/"]["get"]["responses"]["200"]["content"]
    assert "/v1/turns/{turn_id}/commit" in paths
    assert "/v1/turns/{turn_id}/snapshots" in paths
    assert "/v1/turns/{turn_id}/events" in paths


def test_stream_entrypoint_builds_only_stream_path(monkeypatch) -> None:
    settings = StreamSettings()
    store = object()
    trigger = TriggerStub()
    monkeypatch.setattr(stream_api, "ModelTrigger", lambda current_settings: trigger)

    path = stream_api._path_factory(settings, store)  # type: ignore[arg-type]

    assert path.name == "stream"
    assert path.settings is settings
    assert path.store is store
    assert path.trigger is trigger
    assert path.supports_snapshots is True
    assert path.public_metadata()["trigger_reasoning_effort"] == "low"
    assert path.public_metadata()["settled_draft_delay_ms"] == 500
    assert "controller.calls" in path.evaluation_metrics["path_specific"]
    assert "reuse.commit_fallbacks" in path.evaluation_metrics["path_specific"]
