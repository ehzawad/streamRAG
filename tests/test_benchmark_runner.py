from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
runner = importlib.import_module("bench.run_benchmark")
typed_trace = importlib.import_module("bench.typed_trace")


def test_immediate_send_does_not_manufacture_a_complete_snapshot() -> None:
    snapshots = typed_trace.cumulative_typed_trace("complete typed question", 70)

    assert snapshots
    assert all(snapshot.is_final is False for snapshot in snapshots)
    assert snapshots[-1].text != "complete typed question"


def test_post_typing_dwell_exposes_complete_dirty_text_before_send() -> None:
    text = "complete typed question"
    duration_ms = typed_trace.typing_duration_ms(text, 70)
    snapshots = typed_trace.cumulative_typed_trace(
        text,
        70,
        post_typing_dwell_ms=5000,
    )
    complete = [snapshot for snapshot in snapshots if snapshot.is_final]

    assert len(complete) == 1
    assert complete[0].text == text
    assert duration_ms <= complete[0].planned_offset_ms < duration_ms + 5000


def test_exact_final_snapshot_must_complete_before_send() -> None:
    query = "complete typed question"
    schedule = {
        "character_count": len(query),
        "is_final": True,
        "transport_status": "aborted_at_commit",
    }

    assert runner.exact_final_snapshot_completed([schedule], query) is False

    schedule["transport_status"] = "completed"

    assert runner.exact_final_snapshot_completed([schedule], query) is True


def test_full_draft_must_reach_quiet_worker_before_send() -> None:
    query = "complete typed question"
    schedule = {
        "revision": 7,
        "character_count": len(query),
        "is_final": True,
        "transport_status": "completed",
    }
    event = {
        "type": "draft.settled",
        "revision": 7,
        "query": query,
        "state": "starting",
        "benchmark_offset_from_commit_ms": -500.0,
    }
    retrieval = {
        "type": "retrieval.started",
        "revision": 7,
        "query": query,
        "benchmark_offset_from_commit_ms": -499.0,
    }

    assert runner.settled_final_snapshot_observed([schedule], [event, retrieval], query, 1.0)

    event["benchmark_offset_from_commit_ms"] = 1.0
    assert not runner.settled_final_snapshot_observed([schedule], [event, retrieval], query, 1.0)

    event["benchmark_offset_from_commit_ms"] = -500.0
    retrieval["query"] = "rewritten query"
    assert not runner.settled_final_snapshot_observed([schedule], [event, retrieval], query, 1.0)


def test_typed_trace_rejects_negative_post_typing_dwell() -> None:
    with pytest.raises(ValueError, match="post_typing_dwell_ms"):
        typed_trace.cumulative_typed_trace("question", 70, post_typing_dwell_ms=-1)


def complete_gate_inputs() -> dict[str, object]:
    return {
        "smoke": False,
        "reportable_protocol": True,
        "warmup_complete": True,
        "observed_rows": 120,
        "expected_rows": 120,
        "completed_rows": 120,
        "failure_count": 0,
        "timeout_count": 0,
        "drift_observations": 120,
        "drift_violations": 0,
        "max_typing_drift_ms": 100.0,
        "max_observed_abs_drift_ms": 8.0,
        "snapshot_transport_errors": 0,
        "turn_cleanup_failures": 0,
    }


@pytest.mark.parametrize(
    ("overrides", "gate_name"),
    [
        ({"drift_violations": 1}, "timing_drift_gate"),
        ({"snapshot_transport_errors": 1}, "snapshot_transport_gate"),
        ({"timeout_count": 1, "failure_count": 1}, "case_deadline_gate"),
        ({"turn_cleanup_failures": 1}, "turn_cleanup_gate"),
    ],
)
def test_reportability_hard_fails_transport_timing_and_cleanup(
    overrides: dict[str, object], gate_name: str
) -> None:
    inputs = complete_gate_inputs()
    inputs.update(overrides)

    gates = runner.reportability_gates(**inputs)

    assert gates["reportable"] is False
    assert gates[gate_name]["status"] == "missing_or_incomplete"


def test_complete_preregistered_protocol_is_reportable() -> None:
    gates = runner.reportability_gates(**complete_gate_inputs())

    assert gates["reportable"] is True
    assert all(
        gates[name]["status"] == "complete"
        for name in (
            "timing_drift_gate",
            "snapshot_transport_gate",
            "case_deadline_gate",
            "turn_cleanup_gate",
        )
    )


def test_smoke_refuses_unseen_test_queries_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    queries = tmp_path / "test_queries.jsonl"
    queries.write_text(
        json.dumps(
            {
                "id": "hidden-test",
                "query": "must not run",
                "query_time": "2026-01-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_benchmark.py", "--smoke", "--queries", str(queries)],
    )

    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(runner.main())
    assert exc_info.value.code == 2
    assert "unseen test queries are refused" in capsys.readouterr().err
