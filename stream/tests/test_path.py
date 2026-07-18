import pytest

from stream.coordinator import StreamMetrics
from stream.path import reuse_mode


@pytest.mark.parametrize(
    ("fallback", "ready", "revalidations", "completed_ms", "commit_ms", "expected"),
    [
        (False, True, 0, 10.0, 20.0, "precommit_exact"),
        (False, True, 1, 10.0, 20.0, "precommit_revalidated"),
        (
            False,
            False,
            0,
            10.0,
            20.0,
            "presubmit_retrieval_revalidated_at_commit",
        ),
        (False, False, 0, 20.0, 10.0, "inflight_completed_postcommit"),
        (True, False, 0, 10.0, 20.0, "committed_text_retrieval"),
    ],
)
def test_stream_owns_reuse_mode_classification(
    fallback: bool,
    ready: bool,
    revalidations: int,
    completed_ms: float,
    commit_ms: float,
    expected: str,
) -> None:
    metrics = StreamMetrics(
        accepted_from_fallback=fallback,
        accepted_ready_before_commit=ready,
        evidence_revalidations=revalidations,
        accepted_retrieval_completed_ms=completed_ms,
    )

    assert reuse_mode(metrics, commit_ms) == expected
