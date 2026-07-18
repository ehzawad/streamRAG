from naive.api import app
from naive.path import NaiveRagPath
from shared.config import Settings


def test_naive_service_exposes_only_committed_input_surface() -> None:
    paths = app.openapi()["paths"]
    assert "text/html" in paths["/"]["get"]["responses"]["200"]["content"]
    assert "/v1/turns/{turn_id}/commit" in paths
    assert "/v1/turns/{turn_id}/snapshots" not in paths
    assert "/v1/turns/{turn_id}/events" not in paths


def test_naive_entrypoint_builds_only_naive_path() -> None:
    settings = Settings()
    path = NaiveRagPath(settings, object())  # type: ignore[arg-type]
    assert path.name == "naive"
    assert path.supports_snapshots is False
    assert path.public_metadata() == {}
