"""Pinned official-CRAG constants and deterministic text-cleaning helpers."""

from __future__ import annotations

import bz2
import hashlib
import html
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SOURCE_URL = (
    "https://media.githubusercontent.com/media/facebookresearch/CRAG/main/"
    "data/crag_task_1_and_2_dev_v5.jsonl.bz2"
)
SOURCE_SHA256 = "d4c14897d8ea2f450a24e098b595d8247c6575f996f9869d6f27a020fe020618"
SOURCE_VERSION = "crag_task_1_and_2_dev_v5"
LICENSE = "CC BY-NC 4.0"
DOMAINS = ("finance", "movie", "music", "open", "sports")
MAX_GOLD_CHARS = 240
MAX_GOLD_TOKENS = 30
SCRIPT_STYLE_RE = re.compile(
    r"(?is)<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)>"
)
TAG_RE = re.compile(r"(?s)<[^>]+>")
SPACE_RE = re.compile(r"\s+")
ALNUM_RE = re.compile(r"[^a-z0-9]+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: Any) -> str:
    return ALNUM_RE.sub(" ", str(value or "").casefold()).strip()


def jaccard(left: str, right: str) -> float:
    left_tokens = set(normalize_text(left).split())
    right_tokens = set(normalize_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def visible_text(raw_html: str) -> str:
    # Preserve the complete supplied page text. Corpus sizing is performed by
    # selecting or skipping whole documents, never by truncating their contents.
    raw = raw_html or ""
    raw = SCRIPT_STYLE_RE.sub(" ", raw)
    raw = TAG_RE.sub(" ", raw)
    return SPACE_RE.sub(" ", html.unescape(raw)).strip()


def clean_page(page: dict[str, Any]) -> str:
    title = visible_text(page.get("page_name", ""))
    snippet = visible_text(page.get("page_snippet", ""))
    body = visible_text(page.get("page_result", ""))
    normalized = SPACE_RE.sub(" ", f"{title}\n{snippet}\n{body}").strip()
    return normalized


def answer_is_crisp(answer: str) -> bool:
    normalized = normalize_text(answer)
    count = len(normalized.split())
    return (
        2 <= count <= MAX_GOLD_TOKENS
        and len(answer) <= MAX_GOLD_CHARS
        and normalized not in {"yes", "no", "i don t know", "invalid question"}
    )


def evidence_covered(record: dict[str, Any], pages: list[dict[str, Any]]) -> bool:
    haystack = normalize_text(" ".join(clean_page(page) for page in pages))
    answers = [str(record.get("answer") or "")]
    answers.extend(str(item) for item in (record.get("alt_ans") or []))
    return any(answer_is_crisp(answer) and normalize_text(answer) in haystack for answer in answers)


def read_records(path: Path) -> Iterable[dict[str, Any]]:
    with bz2.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on source line {line_number}") from exc
