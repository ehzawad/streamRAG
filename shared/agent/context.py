from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

import tiktoken

from shared.models import Hit

_ENCODING = tiktoken.get_encoding("cl100k_base")

# Same citation syntax as the offline scorer: [doc-id::c0001].
CITATION_MARKER = re.compile(r"\[([^\]\s]+::c\d{4})\]")


def citation_markers(text: str) -> list[str]:
    """Unique citation-shaped markers in first-seen order."""
    return list(dict.fromkeys(CITATION_MARKER.findall(text or "")))


def token_count(text: str) -> int:
    return len(_ENCODING.encode(text))


def fit_hits_to_budget(hits: Iterable[Hit], token_budget: int) -> list[Hit]:
    selected: list[Hit] = []
    used = 0
    for hit in hits:
        required = hit.chunk.token_count + token_count(hit.chunk.title) + 24
        if selected and used + required > token_budget:
            break
        if required > token_budget:
            continue
        selected.append(hit)
        used += required
    return selected


@dataclass(frozen=True)
class EvidencePayload:
    """Rendered evidence text plus the chunk identities that actually fit the budget."""

    text: str
    chunk_ids: tuple[str, ...]
    token_estimate: int


def evidence_payload(hits: Iterable[Hit], token_budget: int) -> EvidencePayload:
    selected = fit_hits_to_budget(hits, token_budget)
    if not selected:
        return EvidencePayload(
            text="No retrieved evidence is available.",
            chunk_ids=(),
            token_estimate=0,
        )
    text = "\n\n".join(
        f"[{hit.chunk.chunk_id}]\n"
        f"Title: {hit.chunk.title}\n"
        f"URL: {hit.chunk.url}\n"
        f"Text: {hit.chunk.text}"
        for hit in selected
    )
    tokens = sum(
        hit.chunk.token_count + token_count(hit.chunk.title) + 24 for hit in selected
    )
    return EvidencePayload(
        text=text,
        chunk_ids=tuple(hit.chunk.chunk_id for hit in selected),
        token_estimate=tokens,
    )


def evidence_block(hits: Iterable[Hit], token_budget: int) -> str:
    return evidence_payload(hits, token_budget).text


def tool_result_json(hits: Iterable[Hit], token_budget: int) -> str:
    selected = fit_hits_to_budget(hits, token_budget)
    return json.dumps(
        {
            "results": [
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "title": hit.chunk.title,
                    "url": hit.chunk.url,
                    "text": hit.chunk.text,
                    "score": round(hit.score, 6),
                }
                for hit in selected
            ]
        },
        ensure_ascii=False,
    )
