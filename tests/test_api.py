from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.api.main as api_main
from app.config import Settings


def test_health_and_dataset_gate(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    isolated = Settings(
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "runtime.sqlite3",
        metrics_log=tmp_path / "requests.jsonl",
        allow_unreviewed_dataset=False,
    )
    monkeypatch.setattr(api_main, "settings", isolated)
    with TestClient(api_main.app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["dataset_status"] == "candidate_pending_human_review"
        assert health.json()["reasoning_effort"] == "medium"
        assert health.json()["trigger_reasoning_effort"] == "low"
        assert health.json()["summary_reasoning_effort"] == "low"
        assert client.get("/v1/turns/unknown/events").status_code == 404
        assert client.get("/v1/runs/unknown/events").status_code == 404
        for endpoint in ("/v1/turns/unknown/events", "/v1/runs/unknown/events"):
            malformed = client.get(endpoint, headers={"Last-Event-ID": "not-an-integer"})
            assert malformed.status_code == 400
            assert "non-negative integer" in malformed.json()["detail"]
            negative = client.get(endpoint, headers={"Last-Event-ID": "-1"})
            assert negative.status_code == 400
        blocked = client.post("/v1/data/sync")
        assert blocked.status_code == 409
        assert "not human-approved" in blocked.json()["detail"]
