#!/usr/bin/env python3
"""Prepare the canonical text-first CRAG evaluation dataset.

The script never downloads data or approves its output. It verifies the official
source checksum, reproduces one manually reviewed question mapping, and builds a
label-free global corpus for the assignment benchmark.
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import shutil
import statistics
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tiktoken

from scripts.crag_source import (
    DOMAINS,
    LICENSE,
    SOURCE_SHA256,
    SOURCE_URL,
    SOURCE_VERSION,
    clean_page,
    jaccard,
    normalize_text,
    read_records,
    sha256_file,
    visible_text,
)
from scripts.stabilization import heuristic_stabilization_class

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "crag_eval"
APPROVAL_STATUS = "candidate_pending_human_review"
SEED = "fundednext-typed-streamrag-crag-eval"
TARGET_CORPUS_DOCUMENTS = 250
MAX_INDEX_POINTS = 1_000
MAX_SUPPORT_POINTS = 20


@dataclass(frozen=True)
class CuratedSpec:
    role: Literal["dev", "test"]
    domain: str
    interaction_id: str
    query: str
    answer: str
    alt_answers: tuple[str, ...]
    supporting_doc_ids: tuple[str, ...]
    evidence_phrases: tuple[str, ...]


# This is the only evaluation selection. Each support page was read manually and
# proves the requested relation, not merely the answer string. Query rewrites fix
# an imprecise boundary, an ungrammatical false premise, or an ambiguous predicate.
CURATED_SPECS: tuple[CuratedSpec, ...] = (
    CuratedSpec(
        "dev",
        "finance",
        "260c5d09-cec1-4792-8438-c5317ae74891",
        "how long does a stock need to be held to make capital gains long term?",
        "more than one year",
        ("over one year", "longer than one year"),
        ("crag-global-9d22ffbe3ca22f9a9858bd6e",),
        ("if you hold the asset for more than one year",),
    ),
    CuratedSpec(
        "dev",
        "movie",
        "1ab0e558-0ace-4264-b248-36161677cff9",
        "which dune movie has better music, 1984 or 2021?",
        "Dune (2021)",
        ("the 2021 Dune", "Dune 2021", "the music for Dune 2021 is more striking"),
        ("crag-global-046532bde5668fd1929c2408",),
        ("Zimmer's Dune music is more striking",),
    ),
    CuratedSpec(
        "dev",
        "music",
        "909fb375-e924-4f8b-8c9d-b39d2402550b",
        (
            "what is the name of the bad bunny album released before nadie sabe lo que "
            "va a pasar mañana?"
        ),
        "Un Verano Sin Ti",
        (),
        ("crag-global-a6deebaec508edb9daefce6e",),
        ("follow-up to his record-smashing Un Verano Sin Ti",),
    ),
    CuratedSpec(
        "dev",
        "open",
        "7499dee8-9270-4dd1-a2bc-69eaf734d13e",
        "Was Taylor Swift's debut album Fearless, released in the United States in 2008?",
        "invalid question",
        (),
        ("crag-global-bd5b1cb86ab3f862b0ed8803",),
        ("On Swift's second album, Fearless (2008)",),
    ),
    CuratedSpec(
        "dev",
        "sports",
        "130aeafb-6a7d-4f5e-8a05-a7293b0fede7",
        "who is currently ranked as the number one mens tennis player in the world?",
        "Novak Djokovic",
        (),
        ("crag-global-a4048eeaa02d759267b94091",),
        ("Novak Djokovic # 1",),
    ),
    CuratedSpec(
        "test",
        "finance",
        "161a89f3-7a70-4e12-a1a2-7832e098b0a7",
        "where did the ceo of salesforce previously work?",
        "Oracle",
        (
            "Oracle Corporation",
            "13 years at Oracle",
            "Marc Benioff spent 13 years at Oracle, before launching Salesforce.",
        ),
        ("crag-global-0f4db44459e874d9300f90a8",),
        ("CEO of the software company Salesforce", "Benioff worked at Oracle for 13 years"),
    ),
    CuratedSpec(
        "test",
        "finance",
        "c0acc154-593c-43b0-b1d1-d046c6b04349",
        "Who stepped down as Apple's CEO in August 2011?",
        "Steve Jobs",
        (),
        ("crag-global-6e07afd4ce9ef9c61f0c659e",),
        ("In August 2011, Steve Jobs stepped down as CEO after 14 years",),
    ),
    CuratedSpec(
        "test",
        "movie",
        "8959e8f9-c24a-4a71-9189-89b1fb01053e",
        (
            "Which film had the larger domestic opening weekend: Harry Potter and the "
            "Half-Blood Prince or Harry Potter and the Deathly Hallows – Part 2?"
        ),
        "Harry Potter and the Deathly Hallows – Part 2",
        ("Harry Potter and the Deathly Hallows: Part 2", "Deathly Hallows Part 2"),
        ("crag-global-14a3d0579cdfdede70eafa7e",),
        ("Half-Blood Prince", "biggest domestic opening weekend of the franchise"),
    ),
    CuratedSpec(
        "test",
        "movie",
        "17403d5f-acf7-410a-bee0-5c931cbaeb07",
        'which actress won an academy award for her role in "black swan"?',
        "Natalie Portman",
        (),
        ("crag-global-4ed5e8a8fb93221e56e3e977",),
        (
            "Natalie Portman has won the best actress Oscar for her performance as the "
            "lead in Black Swan",
        ),
    ),
    CuratedSpec(
        "test",
        "music",
        "bf7d837c-3488-4373-be3d-5ab46a9bc5a1",
        (
            'what album did the killers release in 2004, which included the songs "mr. '
            'brightside" and "jenny was a friend of mine"?'
        ),
        "Hot Fuss",
        (
            "the album Hot Fuss",
            (
                'The Killers released the album "Hot Fuss" in 2004, which included the '
                'songs "Mr. Brightside" and "Jenny Was a Friend of Mine".'
            ),
        ),
        ("crag-global-980bbdc9e21c1c3d8619078d",),
        (
            "Jenny Was a Friend of Mine, Mr. Brightside",
            "Hot Fuss is the debut album",
            "first released in 2004",
        ),
    ),
    CuratedSpec(
        "test",
        "music",
        "f367af85-66c8-4636-89b4-f68bb6277004",
        (
            'what album did kings of leon release in 2013, which included the songs "wait '
            'for me" and "family tree"?'
        ),
        "Mechanical Bull",
        (
            "the album Mechanical Bull",
            (
                'Kings of Leon released the album "Mechanical Bull" in 2013, which included '
                'the songs "Wait for Me" and "Family Tree".'
            ),
        ),
        ("crag-global-67b771c4dd17ec0e2fc485b6",),
        ("Mechanical Bull by Kings of Leon released in 2013", "Wait for Me", "Family Tree"),
    ),
    CuratedSpec(
        "test",
        "open",
        "3c2e454d-10a1-40eb-9534-f9440ecc4c3b",
        "what is the most active volcano in the philippines?",
        "Mayon Volcano",
        ("Mayon",),
        ("crag-global-c0298641c65cc39b09f74477",),
        ("Mt. Mayon is also the most active volcano in the Philippines",),
    ),
    CuratedSpec(
        "test",
        "open",
        "074ec17c-e14b-4671-abb4-51c0b833758d",
        "what are the names of george and amal clooney's twins?",
        "Ella and Alexander Clooney",
        ("Ella and Alexander",),
        ("crag-global-f3d09c83e3f8c21ca94cbab5",),
        ("George Clooney and Amal Clooney's twins Ella and Alexander",),
    ),
    CuratedSpec(
        "test",
        "sports",
        "3183f25b-f868-4352-b605-9158829561d1",
        "As of March 2024, what NFL teams had never made the Super Bowl?",
        "Cleveland Browns, Detroit Lions, Houston Texans, and Jacksonville Jaguars",
        ("Browns, Lions, Texans, and Jaguars", "Browns, Lions, Jaguars, Texans"),
        ("crag-global-6abd5e9a40b58f788f823cc1",),
        (
            "Four NFL Teams Have Never Played In A Super Bowl",
            "the Browns, Lions, Texans, and Jaguars",
        ),
    ),
    CuratedSpec(
        "test",
        "sports",
        "9d6fef40-99f9-4477-abdd-1a9561776d3a",
        (
            "who are the three players with the most home runs in major league baseball "
            "history as of 2022?"
        ),
        "Barry Bonds, Hank Aaron, and Babe Ruth",
        (
            "Barry Bonds, Hank Aaron, Babe Ruth",
            (
                "As of 2022, Barry Bonds, Hank Aaron, and Babe Ruth are the top three players "
                "with the most home runs in Major League Baseball history."
            ),
        ),
        ("crag-global-fc4e1286518d7798eb3ef0a5",),
        ("Barry Bonds 762", "Hank Aaron 755", "Babe Ruth 714"),
    ),
)


@dataclass(frozen=True)
class Candidate:
    interaction_id: str
    source_split: int
    domain: str
    question_type: str
    dynamism: str
    source_query: str
    query: str
    query_time: str
    answer: str
    alt_answers: tuple[str, ...]
    evidence_covered: bool
    document_ids: tuple[str, ...]
    supporting_doc_ids: tuple[str, ...]
    source_pages: tuple[tuple[str, str], ...]

    @property
    def word_count(self) -> int:
        return len(self.query.split())


def stable_hash(*parts: str) -> str:
    payload = "\x1f".join((SEED, *parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def compress_corpus(stage: Path) -> Path:
    source = stage / "documents.jsonl"
    target = stage / "documents.jsonl.bz2"
    with (
        source.open("rb") as input_handle,
        bz2.open(target, "wb", compresslevel=9) as output_handle,
    ):
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
    source.unlink()
    return target


def compact_corpus(
    source_path: Path,
    target_path: Path,
    selected: list[tuple[str, Candidate]],
) -> dict[str, Any]:
    """Select 250 complete pages while staying at or below 1,000 chunks.

    One concise audited evidence page per question is mandatory. Additional
    complete pages are sampled independently of either RAG path and size-balanced
    only as needed to meet the assignment's reproduction-time budget.
    """

    supporting_ids = {doc_id for _role, item in selected for doc_id in item.supporting_doc_ids}
    mandatory_ids = supporting_ids
    encoding = tiktoken.get_encoding("cl100k_base")

    def chunk_totals(row: dict[str, Any]) -> tuple[int, int]:
        text = str(row.get("text") or "")
        token_count = len(encoding.encode(f"{row.get('title', '')}\n{text}".strip()))
        points = 0
        embedded_tokens = 0
        for offset in range(0, token_count, 350):
            chunk_tokens = min(400, token_count - offset)
            if chunk_tokens <= 0:
                break
            points += 1
            embedded_tokens += chunk_tokens
            if offset + 400 >= token_count:
                break
        return points, embedded_tokens

    # Keep offsets and token statistics rather than every complete document in
    # memory. Some official pages are several megabytes long after markup is
    # removed, and the corpus contract forbids shortening them.
    metadata: list[tuple[str, str, int, int, int, bool]] = []
    found_ids: set[str] = set()
    with source_path.open("rb") as source:
        while True:
            offset = source.tell()
            raw_line = source.readline()
            if not raw_line:
                break
            row = json.loads(raw_line)
            doc_id = str(row["doc_id"])
            is_mandatory = doc_id in mandatory_ids
            if is_mandatory:
                found_ids.add(doc_id)
            points, embedded_tokens = chunk_totals(row)
            order_hash = stable_hash(
                "selected-corpus-order" if is_mandatory else "distractor-corpus-order",
                doc_id,
            )
            metadata.append((order_hash, doc_id, offset, points, embedded_tokens, is_mandatory))

    missing = sorted(mandatory_ids - found_ids)
    if missing:
        raise RuntimeError(f"selected source documents are missing: {missing[:10]}")
    mandatory = sorted((item for item in metadata if item[5]), key=lambda item: item[0])
    distractors = sorted((item for item in metadata if not item[5]), key=lambda item: item[0])
    mandatory_points = sum(item[3] for item in mandatory)
    if mandatory_points > MAX_INDEX_POINTS:
        raise RuntimeError(
            "selected evidence pages alone exceed the local Qdrant point target: "
            f"{mandatory_points} > {MAX_INDEX_POINTS}"
        )
    distractors_needed = TARGET_CORPUS_DOCUMENTS - len(mandatory)
    if distractors_needed < 0:
        raise RuntimeError("mandatory selected-question pages exceed the corpus target")
    if len(distractors) < distractors_needed:
        raise RuntimeError("not enough complete distractor documents for the corpus target")

    # Begin with a fixed-hash sample. If it is too large for embedded Qdrant,
    # deterministically replace its largest distractors with the smallest
    # remaining complete documents. This preserves the document count and every
    # mandatory evidence page without shortening any text.
    initial_distractors = distractors[:distractors_needed]
    chosen_distractors = {item[1]: item for item in initial_distractors}
    estimated_points = mandatory_points + sum(item[3] for item in initial_distractors)
    replacements_for_point_cap = 0
    if estimated_points > MAX_INDEX_POINTS:
        largest_chosen = sorted(initial_distractors, key=lambda item: (-item[3], item[0]))
        smallest_reserve = sorted(
            distractors[distractors_needed:], key=lambda item: (item[3], item[0])
        )
        for old, replacement in zip(largest_chosen, smallest_reserve, strict=False):
            if replacement[3] >= old[3]:
                break
            del chosen_distractors[old[1]]
            chosen_distractors[replacement[1]] = replacement
            estimated_points += replacement[3] - old[3]
            replacements_for_point_cap += 1
            if estimated_points <= MAX_INDEX_POINTS:
                break
    if estimated_points > MAX_INDEX_POINTS:
        raise RuntimeError(
            "cannot fit the target number of complete documents under the point target: "
            f"{estimated_points} > {MAX_INDEX_POINTS}"
        )
    chosen = [
        *mandatory,
        *sorted(chosen_distractors.values(), key=lambda item: item[0]),
    ]

    characters = 0
    embedding_tokens = sum(item[4] for item in chosen)
    with source_path.open("rb") as source, target_path.open("w", encoding="utf-8") as output:
        for _order_hash, _doc_id, offset, _points, _tokens, _mandatory in chosen:
            source.seek(offset)
            row = json.loads(source.readline())
            text = str(row.get("text") or "")
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            characters += len(str(row.get("title") or "")) + len(text)
    if estimated_points > MAX_INDEX_POINTS:
        raise RuntimeError(
            f"compacted corpus produces {estimated_points} chunks, above {MAX_INDEX_POINTS}"
        )
    return {
        "documents": len(chosen),
        "characters": characters,
        "embedding_tokens_with_overlap_upper_bound": embedding_tokens,
        "estimated_index_points": estimated_points,
        "full_documents_only": True,
        "support_documents": len(supporting_ids),
        "selected_evidence_documents": len(mandatory_ids),
        "global_distractor_documents": len(chosen) - len(mandatory),
        "target_documents": TARGET_CORPUS_DOCUMENTS,
        "distractors_replaced_for_point_cap": replacements_for_point_cap,
        "max_index_points": MAX_INDEX_POINTS,
    }


def document_id(text: str) -> tuple[str, str]:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"crag-global-{digest[:24]}", digest


def scan_source(
    source: Path,
    documents_path: Path,
    min_words: int,
) -> tuple[list[Candidate], dict[str, Any]]:
    specs_by_id = {item.interaction_id: item for item in CURATED_SPECS}
    if len(specs_by_id) != len(CURATED_SPECS):
        raise RuntimeError("curated interaction IDs must be unique")
    candidates_by_id: dict[str, Candidate] = {}
    content_hashes: set[str] = set()
    urls: set[str] = set()
    counts: Counter[str] = Counter()
    characters = 0
    encoding = tiktoken.get_encoding("cl100k_base")

    def support_point_count(title: str, text: str) -> int:
        token_count = len(encoding.encode(f"{title}\n{text}".strip()))
        if token_count <= 400:
            return 1
        return 1 + (token_count - 400 + 349) // 350

    with documents_path.open("w", encoding="utf-8") as documents:
        for record in read_records(source):
            counts["source_rows"] += 1
            pages = list(record.get("search_results") or [])
            page_ids: list[str] = []
            page_texts: dict[str, str] = {}
            page_titles: dict[str, str] = {}
            source_pages: list[tuple[str, str]] = []
            for page in pages:
                counts["page_rows"] += 1
                text = clean_page(page)
                if not text:
                    counts["empty_pages"] += 1
                    continue
                doc_id, digest = document_id(text)
                page_ids.append(doc_id)
                page_texts.setdefault(doc_id, text)
                title = visible_text(page.get("page_name", ""))
                page_titles.setdefault(doc_id, title)
                url = str(page.get("page_url") or "")
                source_pages.append((title, url))
                if url:
                    urls.add(url)
                if digest in content_hashes:
                    counts["duplicate_content_rows"] += 1
                    continue
                content_hashes.add(digest)
                characters += len(title) + len(text)
                documents.write(
                    json.dumps(
                        {
                            "doc_id": doc_id,
                            "title": title,
                            "url": url,
                            "snippet": visible_text(page.get("page_snippet", "")),
                            "text": text,
                            "page_last_modified": str(page.get("page_last_modified") or ""),
                            "snapshot_query_time": str(record.get("query_time") or ""),
                            "domain": str(record.get("domain") or "unknown"),
                            "content_sha256": digest,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            interaction_id = str(record.get("interaction_id") or "")
            spec = specs_by_id.get(interaction_id)
            if spec is None:
                continue
            counts["curated_source_rows"] += 1
            if interaction_id in candidates_by_id:
                raise RuntimeError(f"duplicate curated interaction in source: {interaction_id}")
            if record.get("popularity"):
                raise RuntimeError(f"curated interaction is not a web question: {interaction_id}")
            split = record.get("split")
            domain = str(record.get("domain") or "")
            expected_split = 0 if spec.role == "dev" else 1
            if split != expected_split or domain != spec.domain or domain not in DOMAINS:
                raise RuntimeError(
                    f"curated source role/domain mismatch for {interaction_id}: "
                    f"split={split}, domain={domain}"
                )
            if len(spec.query.split()) < min_words:
                raise RuntimeError(f"curated query is below --min-words: {interaction_id}")
            missing_supports = set(spec.supporting_doc_ids) - page_texts.keys()
            if missing_supports:
                raise RuntimeError(
                    f"curated support is not in the interaction's pages: "
                    f"{interaction_id}: {sorted(missing_supports)}"
                )
            support_haystack = normalize_text(
                " ".join(page_texts[doc_id] for doc_id in spec.supporting_doc_ids)
            )
            missing_phrases = [
                phrase
                for phrase in spec.evidence_phrases
                if normalize_text(phrase) not in support_haystack
            ]
            if missing_phrases:
                raise RuntimeError(
                    f"curated evidence phrase is missing for {interaction_id}: {missing_phrases}"
                )
            for doc_id in spec.supporting_doc_ids:
                points = support_point_count(page_titles.get(doc_id, ""), page_texts[doc_id])
                if points > MAX_SUPPORT_POINTS:
                    raise RuntimeError(
                        f"curated support exceeds {MAX_SUPPORT_POINTS} points: "
                        f"{interaction_id}/{doc_id}={points}"
                    )
            source_query = str(record.get("query") or "").strip()
            candidates_by_id[interaction_id] = Candidate(
                interaction_id=interaction_id,
                source_split=int(split),
                domain=domain,
                question_type=str(record.get("question_type") or "unknown"),
                dynamism=str(record.get("static_or_dynamic") or "unknown"),
                source_query=source_query,
                query=spec.query,
                query_time=str(record.get("query_time") or ""),
                answer=spec.answer,
                alt_answers=spec.alt_answers,
                evidence_covered=True,
                document_ids=tuple(dict.fromkeys(page_ids)),
                supporting_doc_ids=spec.supporting_doc_ids,
                source_pages=tuple(dict.fromkeys(source_pages)),
            )
    missing_interactions = [
        item.interaction_id for item in CURATED_SPECS if item.interaction_id not in candidates_by_id
    ]
    if missing_interactions:
        raise RuntimeError(f"curated interactions are missing from source: {missing_interactions}")
    candidates = [candidates_by_id[item.interaction_id] for item in CURATED_SPECS]
    return candidates, {
        **counts,
        "unique_documents": len(content_hashes),
        "unique_urls": len(urls),
        "characters": characters,
    }


def choose_candidates(
    candidates: list[Candidate],
) -> list[tuple[str, Candidate]]:
    by_id = {item.interaction_id: item for item in candidates}
    selected = [(spec.role, by_id[spec.interaction_id]) for spec in CURATED_SPECS]
    role_domain_counts = Counter((role, item.domain) for role, item in selected)
    expected_counts = {
        **{("dev", domain): 1 for domain in DOMAINS},
        **{("test", domain): 2 for domain in DOMAINS},
    }
    if role_domain_counts != Counter(expected_counts):
        raise RuntimeError(f"curated role/domain balance is invalid: {role_domain_counts}")
    return selected


def distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(key) or "unknown") for row in rows).items()))


def write_candidate_outputs(
    stage: Path,
    selected: list[tuple[str, Candidate]],
    source: Path,
    source_stats: dict[str, Any],
    corpus_stats: dict[str, Any],
    expected_source_sha256: str,
    min_words: int,
) -> None:
    dev_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    test_gold: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    role_counts: Counter[str] = Counter()
    for role, item in selected:
        role_counts[role] += 1
        public_id = f"crag-text-{role}-{role_counts[role]:03d}"
        stabilization_class, stabilization_reason = heuristic_stabilization_class(
            item.query, item.question_type
        )
        common = {
            "id": public_id,
            "query": item.query,
            "query_time": item.query_time,
            "domain": item.domain,
            "question_type": item.question_type,
            "dynamism": item.dynamism,
            "word_count": item.word_count,
            "stabilization_class": stabilization_class,
            "stabilization_reason": stabilization_reason,
            "stabilization_confidence": "low",
            "stabilization_review_status": "heuristic_pending_manual_review",
        }
        if role == "dev":
            dev_rows.append(
                {
                    **common,
                    "answer": item.answer,
                    "alt_answers": list(item.alt_answers),
                    "evidence_covered": item.evidence_covered,
                    "supporting_doc_ids": list(item.supporting_doc_ids),
                }
            )
        else:
            test_rows.append(common)
            test_gold.append(
                {
                    "id": public_id,
                    "answer": item.answer,
                    "alt_answers": list(item.alt_answers),
                    "source": "curated from official CRAG gold and audited source evidence",
                    "supporting_doc_ids": list(item.supporting_doc_ids),
                    "acceptable_supporting_doc_ids": [],
                    "support_review_status": (
                        "contradiction_evidence_pending_human_review"
                        if item.question_type == "false_premise"
                        else "direct_evidence_pending_human_review"
                    ),
                }
            )
        manifest.append(
            {
                "id": public_id,
                "role": role,
                "source_interaction_id": item.interaction_id,
                "source_split": item.source_split,
                "source_document_ids": list(item.document_ids),
                "exact_supporting_doc_ids": list(item.supporting_doc_ids),
                "evidence_covered_in_own_search_pages": item.evidence_covered,
                "query_curated_from_source": item.query != item.source_query,
                "word_count": item.word_count,
                "stabilization_class": stabilization_class,
                "stabilization_confidence": "low",
                "stabilization_review_status": "heuristic_pending_manual_review",
            }
        )
        review.append(
            {
                **common,
                "role": role,
                "answer": item.answer,
                "alt_answers": list(item.alt_answers),
                "evidence_covered": item.evidence_covered,
                "source_query": item.source_query,
                "source_pages": list(item.source_pages),
                "supporting_doc_ids": list(item.supporting_doc_ids),
            }
        )

    write_jsonl(stage / "dev_queries.jsonl", dev_rows)
    write_jsonl(stage / "test_queries.jsonl", test_rows)
    write_jsonl(stage / "test_gold.jsonl", test_gold)

    cross_near_duplicates = [
        {
            "dev": dev["id"],
            "test": test["id"],
            "jaccard": round(jaccard(dev["query"], test["query"]), 4),
        }
        for dev in dev_rows
        for test in test_rows
        if jaccard(dev["query"], test["query"]) >= 0.85
    ]
    test_label_keys = sorted(
        {"answer", "alt_ans", "alt_answers", "gold", "expected_answer"}
        & set().union(*(row.keys() for row in test_rows))
    )
    exact_query_content_hits: dict[str, list[str]] = defaultdict(list)
    needles = {row["id"]: normalize_text(row["query"]) for row in [*dev_rows, *test_rows]}
    with (stage / "documents.jsonl").open(encoding="utf-8") as documents:
        for raw_line in documents:
            document = json.loads(raw_line)
            haystack = normalize_text(
                f"{document.get('title', '')} {document.get('snippet', '')} "
                f"{document.get('text', '')}"
            )
            for query_id, needle in needles.items():
                if needle and needle in haystack:
                    exact_query_content_hits[query_id].append(document["doc_id"])
    audit = {
        "status": "pass" if not test_label_keys and not cross_near_duplicates else "fail",
        "approval_status": APPROVAL_STATUS,
        "test_label_keys_exposed_in_test_queries": test_label_keys,
        "forbidden_query_or_label_wrapper_fields_in_documents": [],
        "cross_split_query_near_duplicates_at_0_85": cross_near_duplicates,
        "exact_query_strings_found_as_natural_document_content": exact_query_content_hits,
        "note": (
            "Natural page text may contain a question string; no query, answer, split, or gold "
            "wrapper field is written to the retrievable corpus. Test golds remain scorer-only."
        ),
    }
    (stage / "leakage_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source_digest = sha256_file(source)
    summary = {
        "source": {
            "name": SOURCE_VERSION,
            "official_url": SOURCE_URL,
            "filename": source.name,
            "sha256": source_digest,
            "expected_sha256": expected_source_sha256,
            "license": LICENSE,
            **source_stats,
        },
        "selection": {
            "dataset_id": "crag-text-eval",
            "approval_status": APPROVAL_STATUS,
            "seed": SEED,
            "dev_questions": len(dev_rows),
            "test_questions": len(test_rows),
            "minimum_query_words": min_words,
            "selection_method": "fixed manual evidence audit",
            "selection_independent_of_path_outputs": True,
            "stabilization_class_assigned_after_selection": True,
            "stabilization_class_status": "heuristic_pending_manual_review",
            "stabilization_class_confidence": "low",
            "text_input_contract": {
                "snapshot_interval_ms": 400,
                "post_typing_dwell_ms": 5000,
                "settled_draft_delay_ms": 500,
                "snapshots": (
                    "changed-only cumulative dirty text; unchanged 400 ms ticks emit no "
                    "snapshot; after 500 ms unchanged, the latest delivered draft starts "
                    "exact speculative retrieval without generating an answer"
                ),
                "commit": "full text at a higher revision",
                "answer_before_send": False,
            },
            "speech_latency_excluded": ["ASR", "endpoint detection", "trailing silence"],
        },
        "dev": {
            "domains": distribution(dev_rows, "domain"),
            "question_types": distribution(dev_rows, "question_type"),
            "stabilization_classes": distribution(dev_rows, "stabilization_class"),
            "median_words": statistics.median(row["word_count"] for row in dev_rows),
        },
        "test": {
            "domains": distribution(test_rows, "domain"),
            "question_types": distribution(test_rows, "question_type"),
            "stabilization_classes": distribution(test_rows, "stabilization_class"),
            "median_words": statistics.median(row["word_count"] for row in test_rows),
            "gold_file_is_scorer_only": True,
            "supporting_doc_ids_recorded_in_gold": True,
        },
        "corpus": {
            "strategy": (
                "one concise audited evidence page per question plus a fixed-hash "
                "full-document sample; largest distractors are replaced with smaller whole "
                "pages only as needed to meet the assignment runtime target"
            ),
            "filename": "documents.jsonl.bz2",
            "compression": "bzip2",
            "query_neighborhood_isolation": False,
            **corpus_stats,
        },
        "leakage_audit": audit,
    }
    (stage / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (stage / "selection_manifest.json").write_text(
        json.dumps(
            {
                "source": summary["source"],
                "selection_seed": SEED,
                "rules": [
                    "official split 0 development and split 1 test",
                    "human-authored web questions only",
                    f"at least {min_words} whitespace-delimited words",
                    "every item has manually audited predicate-level evidence in its own pages",
                    f"each audited support page uses at most {MAX_SUPPORT_POINTS} index points",
                    "one development and two test questions per domain",
                    "selection never uses Naive RAG or StreamRAG outputs",
                    "stabilization classes are assigned only after selection and require review",
                ],
                "items": manifest,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence_lookup_instruction = (
        "   '<document-id>' data/crag_eval/documents.jsonl.bz2`) and verify "
        "wording, time anchor, expected"
    )
    review_lines = [
        "# CRAG text evaluation dataset — human review sheet",
        "",
        "> **Status: PENDING HUMAN REVIEW.** Do not freeze or run the unseen test split",
        "> until every item and the global-corpus construction have been reviewed.",
        "",
        "This is a dataset-quality review, not an application or model-output review. Work",
        "only from each question, expected answer, source pages, and the proposed support",
        "document. Do not inspect Naive RAG or StreamRAG predictions while deciding whether",
        "an item belongs in the evaluation set.",
        "",
        "## What the fields mean",
        "",
        "- **Gold** is the expected answer; **Aliases** are equivalent scorer-accepted forms.",
        "- **Own-page evidence covered** says whether a selected source page supports the",
        "  answer, or supports the intended abstention for a false premise.",
        "- **Proposed supporting document IDs** name the exact committed corpus pages to",
        "  verify; they are candidates until this review is complete.",
        "- **Candidate stabilization class** predicts when the information need becomes",
        "  clear while a person types. It is a low-confidence review aid, not a result.",
        "- **Role** is `dev` for visible development items and `test` for the sealed final",
        "  comparison. Accepting it confirms only that split assignment.",
        "",
        "## How to review",
        "",
        "1. Find each proposed ID in `documents.jsonl.bz2` (for example, `bzgrep",
        evidence_lookup_instruction,
        "   answer, and aliases against that committed text and the listed source pages.",
        "2. Read the query left to right and judge whether its stabilization class is",
        "   plausible without viewing either path's outputs.",
        "3. Confirm the development/test role, tick the six item boxes, and record any",
        "   correction in the dataset change before approval.",
        "4. Complete the corpus checklist at the end. Ticking boxes documents review; the",
        "   approval status remains separate until all corrections are resolved.",
        "",
    ]
    for item in review:
        metadata = f"{item['domain']} / {item['question_type']} / {item['dynamism']}"
        coverage = "yes" if item["evidence_covered"] else "no; false-premise control"
        review_lines.extend(
            [
                f"## {item['id']} — {item['role']}",
                "",
                f"- **Words:** {item['word_count']}",
                f"- **Domain / type / dynamism:** {metadata}",
                f"- **Query:** {item['query']}",
                *(
                    [f"- **Original source query:** {item['source_query']}"]
                    if item["source_query"] != item["query"]
                    else []
                ),
                f"- **Candidate stabilization class:** {item['stabilization_class']}",
                f"- **Classification confidence:** {item['stabilization_confidence']}",
                f"- **Heuristic reason:** {item['stabilization_reason']}",
                f"- **Gold:** {item['answer']}",
                f"- **Aliases:** {', '.join(item['alt_answers']) or '—'}",
                f"- **Own-page evidence covered:** {coverage}",
                "- **Proposed supporting document IDs:** "
                + (", ".join(item["supporting_doc_ids"]) or "none"),
                "- **Original CRAG pages:**",
            ]
        )
        review_lines.extend(
            f"  - {(title or 'Untitled').replace(chr(10), ' ')}: <{url}>"
            for title, url in item["source_pages"]
        )
        review_lines.extend(
            [
                "- [ ] Question wording and time anchor are clear",
                "- [ ] Expected answer and aliases are factually correct",
                "- [ ] Committed support document proves the answer or intended abstention",
                "- [ ] Typing can plausibly stabilize as classified",
                "- [ ] Stabilization class accepted or corrected without model outputs",
                "- [ ] Split assignment accepted (development or test)",
                "",
            ]
        )
    review_lines.extend(
        [
            "## Global corpus checklist",
            "",
            "- [ ] The corpus contains 250 complete CRAG source pages; no page is shortened",
            "- [ ] The 15 preselected support documents and 235 distractors are acceptable",
            (
                "- [ ] Query, answer, split, and gold wrapper fields are absent from "
                "retrievable documents"
            ),
            "- [ ] Test labels remain scorer-only and are not available to either application path",
            "- [ ] The expected 1,000 chunks fit the local-Qdrant runtime target",
            "- [ ] Every item review is complete and all requested corrections are resolved",
            "",
        ]
    )
    (stage / "REVIEW_SHEET.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    development_count = len(dev_rows)
    test_count = len(test_rows)
    document_count = f'{corpus_stats["documents"]:,}'
    point_count = f'{corpus_stats["estimated_index_points"]:,}'
    readme = f"""# CRAG evaluation data

This directory is the complete, committed dataset used by both application paths.
It contains {development_count} development questions, {test_count} sealed test questions, and
{document_count} complete CRAG pages. Only `documents.jsonl.bz2` is embedded: its
full pages produce {point_count} chunks under the fixed indexing contract. No page
is shortened.

## Status

`candidate_pending_human_review` means the dataset is proposed but not frozen.
Review every item and the corpus checklist in `REVIEW_SHEET.md` without viewing
either path's predictions. The final unseen benchmark must wait until corrections
are resolved and the status is deliberately changed to `approved_frozen`.

## Files

- `documents.jsonl.bz2`: retrievable full-page corpus; contains no query or gold wrappers.
- `dev_queries.jsonl`: visible questions for development and smoke benchmarks.
- `test_queries.jsonl`: sealed questions without answers.
- `test_gold.jsonl`: scorer-only expected answers and accepted evidence IDs.
- `dataset_summary.json`: corpus statistics, input contract, and approval status.
- `selection_manifest.json`: provenance, split roles, and support-document mapping.
- `leakage_audit.json`: checks that labels and wrapper fields are not retrievable.
- `REVIEW_SHEET.md`: human QA checklist for questions, evidence, classes, and corpus.
- `checksums.sha256`: integrity hashes for every committed dataset artifact.

The selection is fixed independently of Naive RAG and StreamRAG outputs.
Development items may be debugged; sealed test labels must remain scorer-only.
See `../../docs/DATASET.md` for the construction and approval policy.
"""
    (stage / "README.md").write_text(readme, encoding="utf-8")
    compress_corpus(stage)
    checksum_files = [
        "README.md",
        "REVIEW_SHEET.md",
        "dataset_summary.json",
        "dev_queries.jsonl",
        "documents.jsonl.bz2",
        "leakage_audit.json",
        "selection_manifest.json",
        "test_gold.jsonl",
        "test_queries.jsonl",
    ]
    checksums = [f"{sha256_file(stage / name)}  {name}" for name in checksum_files]
    (stage / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reproduce the fixed canonical text-first CRAG evaluation dataset"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-source-sha256", default=SOURCE_SHA256)
    parser.add_argument("--min-words", type=int, default=8)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"source does not exist: {args.source}")
    if args.min_words < 1:
        parser.error("min words must be positive")
    actual_digest = sha256_file(args.source)
    if actual_digest != args.expected_source_sha256:
        raise SystemExit(
            f"source checksum mismatch: expected {args.expected_source_sha256}, got {actual_digest}"
        )
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit(f"output already exists; refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        full_corpus = stage / "documents.full.jsonl"
        candidates, stats = scan_source(args.source, full_corpus, args.min_words)
        selected = choose_candidates(candidates)
        corpus_stats = compact_corpus(
            full_corpus,
            stage / "documents.jsonl",
            selected,
        )
        full_corpus.unlink()
        write_candidate_outputs(
            stage,
            selected,
            args.source,
            stats,
            corpus_stats,
            args.expected_source_sha256,
            args.min_words,
        )
        stage.replace(output)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(
        json.dumps(
            {
                "output": str(output),
                "approval_status": APPROVAL_STATUS,
                "documents": corpus_stats["documents"],
                "estimated_index_points": corpus_stats["estimated_index_points"],
                "dev_questions": sum(item.role == "dev" for item in CURATED_SPECS),
                "test_questions": sum(item.role == "test" for item in CURATED_SPECS),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
