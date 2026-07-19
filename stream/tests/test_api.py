from stream.api import app
from stream.config import StreamSettings
from stream.path import StreamRagPath


class TriggerStub:
    async def close(self) -> None:
        return None


def test_stream_service_exposes_typed_snapshot_surface() -> None:
    paths = app.openapi()["paths"]
    assert "application/json" in paths["/"]["get"]["responses"]["200"]["content"]
    assert "/v1/turns/{turn_id}/commit" in paths
    assert "/v1/turns/{turn_id}/snapshots" in paths
    assert "/v1/turns/{turn_id}/events" in paths


def test_stream_entrypoint_builds_only_stream_path() -> None:
    settings = StreamSettings()
    path = StreamRagPath(
        settings,
        object(),  # type: ignore[arg-type]
        TriggerStub(),  # type: ignore[arg-type]
    )
    assert path.name == "stream"
    assert path.supports_snapshots is True
    assert path.public_metadata()["trigger_reasoning_effort"] == "low"
    assert path.public_metadata()["settled_draft_delay_ms"] == 500
    assert "controller.calls" in path.evaluation_metrics["path_specific"]
    assert "reuse.commit_fallbacks" in path.evaluation_metrics["path_specific"]
