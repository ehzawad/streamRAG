from __future__ import annotations

from scripts.crag_source import clean_page


def test_clean_page_strips_markup_without_shortening_content() -> None:
    body = "A" * 210_000 + " complete-tail-marker"
    cleaned = clean_page(
        {
            "page_name": "Full page",
            "page_snippet": "Complete snippet",
            "page_result": f"<main>{body}</main>",
        }
    )

    assert cleaned.endswith("complete-tail-marker")
    assert body in cleaned
    assert len(cleaned) > 210_000
