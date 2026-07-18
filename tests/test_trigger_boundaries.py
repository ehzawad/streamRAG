from app.stream.coordinator import has_terminal_boundary


def test_terminal_punctuation_can_recheck_after_one_new_word() -> None:
    assert has_terminal_boundary("when was the great depression?", "when was the great")


def test_unchanged_or_incomplete_text_is_not_a_new_boundary() -> None:
    assert not has_terminal_boundary("when was the great", "when was")
    assert not has_terminal_boundary("when was the great?", "when was the great?")
