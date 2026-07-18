from __future__ import annotations

import re
from typing import Literal

StabilizationClass = Literal[
    "early_stabilization",
    "late_stabilization",
    "revision_or_ambiguity",
]

AMBIGUITY_MARKERS = (" rather than ", " instead of ", " actually ", " i mean ")
GENERIC_WORDS = {
    "a",
    "an",
    "are",
    "as",
    "at",
    "be",
    "by",
    "company",
    "current",
    "currently",
    "date",
    "did",
    "do",
    "does",
    "film",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "is",
    "movie",
    "name",
    "number",
    "of",
    "on",
    "percent",
    "percentage",
    "song",
    "team",
    "the",
    "to",
    "total",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "year",
}
TAIL_CONDITION_MARKERS = {
    "after",
    "before",
    "between",
    "excluding",
    "first",
    "last",
    "latest",
    "most",
    "today",
}


def _tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", query.casefold())


def heuristic_stabilization_class(query: str, question_type: str) -> tuple[StabilizationClass, str]:
    """Return a low-confidence candidate label for manual review.

    The label is assigned after question selection and never uses retrieval or path
    outputs. ArXiv 2606.20113 finds that entity position matters more than reasoning
    complexity and that question type explains only a small share of rank variance.
    These lexical rules therefore preregister coarse reporting strata only; measured
    prefix retrieval and accepted-evidence traces remain authoritative.
    """

    normalized = f" {' '.join(query.casefold().split())} "
    words = _tokens(query)
    if not words:
        return "late_stabilization", "empty lexical analysis; manual classification required"

    if any(marker in normalized for marker in AMBIGUITY_MARKERS):
        return (
            "revision_or_ambiguity",
            "explicit correction/contrast language can overturn an earlier interpretation",
        )
    if " or " in normalized:
        or_index = words.index("or") if "or" in words else len(words)
        if or_index / len(words) >= 0.5:
            return (
                "revision_or_ambiguity",
                "a late disjunction introduces another candidate after a plausible prefix",
            )

    anchor_index = next(
        (index for index, word in enumerate(words) if word not in GENERIC_WORDS),
        len(words) - 1,
    )
    anchor_fraction = (anchor_index + 1) / len(words)
    tail = set(words[-4:])
    late_numeric_constraint = any(
        any(character.isdigit() for character in word) for word in words[-3:]
    )
    late_constraint = bool(tail & TAIL_CONDITION_MARKERS) or late_numeric_constraint

    # The paper's only reliable type-level extreme is set questions stabilizing
    # latest; aggregation and comparison are often early because entities occur up front.
    if question_type == "set" or anchor_fraction > 0.55 or late_constraint:
        return (
            "late_stabilization",
            "the first lexical anchor or a material constraint occurs late in the query",
        )
    return (
        "early_stabilization",
        "a non-generic lexical anchor appears in the first half without a late constraint",
    )
