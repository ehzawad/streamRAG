import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

        oversized_turn = "t" * 129
        snapshot_payload = {
            "session_id": "session",
            "path": "stream",
            "revision": 1,
            "text": "complete question?",
        }
        commit_payload = {**snapshot_payload, "path": "naive", "query_time": ""}
        responses = [
            client.post(f"/v1/turns/{oversized_turn}/snapshots", json=snapshot_payload),
            client.post(f"/v1/turns/{oversized_turn}/commit", json=commit_payload),
            client.get(f"/v1/turns/{oversized_turn}/events"),
            client.delete(f"/v1/turns/{oversized_turn}"),
        ]
        assert all(response.status_code == 422 for response in responses)
        assert not api_main.app.state.runtime.turns
        assert not api_main.app.state.runtime.terminal_turns
        assert not api_main.app.state.runtime.events._channels


def test_health_readiness_includes_dataset_approval_gate(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    isolated = Settings(
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "runtime.sqlite3",
        metrics_log=tmp_path / "requests.jsonl",
        allow_unreviewed_dataset=False,
    )
    monkeypatch.setattr(api_main, "settings", isolated)
    with TestClient(api_main.app) as client:
        runtime = api_main.app.state.runtime
        monkeypatch.setattr(
            runtime.store,
            "get_collection",
            AsyncMock(return_value=SimpleNamespace(points_count=4)),
        )
        monkeypatch.setattr(
            runtime.store.state,
            "metadata",
            AsyncMock(
                return_value={
                    "version": 1,
                    "index_checksum": "checksum",
                    "desired_chunks": 4,
                    "index_source_sha256": "current-source",
                    "ready": True,
                }
            ),
        )
        monkeypatch.setattr(
            api_main,
            "_dataset_health_state",
            lambda: ("current-source", True, "candidate_pending_human_review"),
        )

        body = client.get("/v1/health").json()

        assert body["dataset_approval_allowed"] is False
        assert body["dataset_checksums_valid"] is True
        assert body["index_metadata_ready"] is True
        assert body["index_matches_current_corpus"] is True
        assert body["index_ready"] is False
        assert body["ok"] is False

        monkeypatch.setattr(
            api_main,
            "_dataset_health_state",
            lambda: ("current-source", True, "approved_frozen"),
        )
        approved = client.get("/v1/health").json()
        assert approved["dataset_approval_allowed"] is True
        assert approved["index_ready"] is True
        assert approved["ok"] is True

        monkeypatch.setattr(
            api_main,
            "settings",
            replace(isolated, allow_unreviewed_dataset=True),
        )
        monkeypatch.setattr(
            api_main,
            "_dataset_health_state",
            lambda: ("current-source", True, "candidate_pending_human_review"),
        )
        explicitly_allowed = client.get("/v1/health").json()
        assert explicitly_allowed["dataset_approval_allowed"] is True
        assert explicitly_allowed["index_ready"] is True
        assert explicitly_allowed["ok"] is True


def test_data_status_keeps_startup_code_provenance_while_dataset_stays_live(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    isolated = Settings(
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "runtime.sqlite3",
        metrics_log=tmp_path / "requests.jsonl",
        allow_unreviewed_dataset=True,
    )
    monkeypatch.setattr(api_main, "settings", isolated)
    with TestClient(api_main.app) as client:
        startup = dict(api_main.app.state.runtime.fingerprints)
        live_dataset = {
            "dataset_checksum": "live-evaluation-manifest",
            "serving_dataset_checksum": "live-serving-manifest",
            "freeze_id": "live-freeze",
            "dataset_sha256": "live-dataset",
            "documents_sha256": "live-documents",
        }
        drifted_runtime = {
            "backend_source_sha256": "post-start-source",
            "config_hash": "post-start-config",
            **live_dataset,
        }
        monkeypatch.setattr(
            api_main,
            "runtime_fingerprints",
            lambda _settings: drifted_runtime,
            raising=False,
        )
        monkeypatch.setattr(
            api_main,
            "dataset_fingerprints",
            lambda _settings, _snapshot=None: live_dataset,
        )

        body = client.get("/v1/data/status").json()

        assert body["backend_source_sha256"] == startup["backend_source_sha256"]
        assert body["config_hash"] == startup["config_hash"]
        assert body["backend_source_sha256"] != "post-start-source"
        assert body["config_hash"] != "post-start-config"
        for key, value in live_dataset.items():
            assert body[key] == value


def _write_status_dataset(root: Path, document_text: str) -> None:
    root.mkdir(exist_ok=True)
    files = {
        "dataset_summary.json": json.dumps(
            {"selection": {"approval_status": "candidate_pending_human_review"}}
        ),
        "dev_queries.jsonl": "{}\n",
        "documents.jsonl": document_text,
        "leakage_audit.json": "{}\n",
        "selection_manifest.json": "{}\n",
        "test_gold.jsonl": "{}\n",
        "test_queries.jsonl": "{}\n",
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    manifest = "".join(
        f"{hashlib.sha256(content.encode()).hexdigest()}  {name}\n"
        for name, content in sorted(files.items())
    )
    (root / "checksums.sha256").write_text(manifest, encoding="utf-8")


def test_data_status_fingerprints_are_bound_to_one_verified_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    dataset = tmp_path / "dataset"
    _write_status_dataset(dataset, "snapshot-a\n")
    isolated = Settings(
        dataset_dir=dataset,
        qdrant_path=tmp_path / "qdrant",
        runtime_db=tmp_path / "runtime.sqlite3",
        metrics_log=tmp_path / "requests.jsonl",
        allow_unreviewed_dataset=True,
    )
    captured = api_main.capture_dataset_snapshot(dataset)
    original_fingerprints = api_main.dataset_fingerprints
    monkeypatch.setattr(api_main, "settings", isolated)
    monkeypatch.setattr(api_main, "capture_dataset_snapshot", lambda _root: captured)

    def swap_then_fingerprint(_settings, snapshot=None):
        assert snapshot is captured
        _write_status_dataset(dataset, "snapshot-b\n")
        return original_fingerprints(isolated, snapshot)

    monkeypatch.setattr(api_main, "dataset_fingerprints", swap_then_fingerprint)

    state = api_main._dataset_status_state(
        {"backend_source_sha256": "startup-source", "config_hash": "startup-config"}
    )
    reported = state["fingerprints"]
    current = original_fingerprints(isolated)

    assert state["checksums_valid"] is True
    assert reported["backend_source_sha256"] == "startup-source"
    assert reported["config_hash"] == "startup-config"
    assert reported["documents_sha256"] == captured.documents_sha256
    assert reported["serving_dataset_checksum"] == captured.serving_dataset_checksum
    assert reported["dataset_checksum"] == captured.dataset_checksum
    assert reported["freeze_id"] == captured.freeze_id
    assert reported["dataset_sha256"] != current["dataset_sha256"]
    assert reported["documents_sha256"] != current["documents_sha256"]
    assert state["current_index_source"] != api_main.index_source_sha256(isolated)
