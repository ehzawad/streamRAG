from stream.snapshot import SnapshotAnalyzer


def test_snapshot_delta_handles_append_and_correction() -> None:
    analyzer = SnapshotAnalyzer()
    appended = analyzer.analyze("who is", "who is the nba leader")
    corrected = analyzer.analyze("who is the nba", "who was the nba")
    assert appended.append_only
    assert appended.new_words == 3
    assert appended.word_count == 5
    assert not corrected.append_only
    assert corrected.common_prefix_chars == len("who ")
    assert appended.fingerprint != corrected.fingerprint
