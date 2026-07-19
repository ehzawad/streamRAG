from __future__ import annotations

import hashlib
import json
import sys

import pytest

import scripts.prepare_crag_text_global as prepare
from scripts.prepare_crag_text_global import Candidate, compact_corpus


class WordEncoding:
    def encode(self, text: str) -> list[str]:
        return text.split()


def candidate(supporting_doc_id: str) -> Candidate:
    return Candidate(
        interaction_id="interaction",
        source_split=0,
        domain="open",
        question_type="simple",
        dynamism="static",
        source_query="source question",
        query="curated question",
        query_time="2024-01-01",
        answer="answer",
        alt_answers=(),
        evidence_covered=True,
        document_ids=(supporting_doc_id,),
        supporting_doc_ids=(supporting_doc_id,),
        source_pages=(),
    )


def test_compact_corpus_keeps_complete_support_and_fixed_document_count(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "documents.full.jsonl"
    target = tmp_path / "documents.jsonl"
    rows = [
        {"doc_id": "support", "title": "Support", "text": "complete evidence page"},
        {"doc_id": "distractor-a", "title": "A", "text": "complete page a"},
        {"doc_id": "distractor-b", "title": "B", "text": "complete page b"},
        {"doc_id": "distractor-c", "title": "C", "text": "complete page c"},
    ]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(prepare, "TARGET_CORPUS_DOCUMENTS", 3)
    monkeypatch.setattr(prepare, "MAX_INDEX_POINTS", 3)
    monkeypatch.setattr(prepare.tiktoken, "get_encoding", lambda _name: WordEncoding())

    stats = compact_corpus(source, target, [("dev", candidate("support"))])
    selected = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]

    assert len(selected) == 3
    assert any(row["doc_id"] == "support" for row in selected)
    assert all(row in rows for row in selected)
    assert stats == {
        "documents": 3,
        "characters": sum(len(row["title"]) + len(row["text"]) for row in selected),
        "embedding_tokens_with_overlap_upper_bound": 12,
        "estimated_index_points": 3,
        "full_documents_only": True,
        "support_documents": 1,
        "selected_evidence_documents": 1,
        "global_distractor_documents": 2,
        "target_documents": 3,
        "distractors_replaced_for_point_cap": 0,
        "max_index_points": 3,
    }


def test_main_builds_in_an_atomic_staging_directory(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.jsonl.bz2"
    source.write_bytes(b"synthetic pinned source")
    output = tmp_path / "var" / "rebuilt-crag-eval"
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    def fake_scan(_source, full_corpus, min_words):
        assert min_words == 8
        full_corpus.write_text("{}\n", encoding="utf-8")
        return [], {"source_rows": 1}

    def fake_compact(_source, target, selected):
        assert selected == []
        target.write_text("{}\n", encoding="utf-8")
        return {"documents": 0, "estimated_index_points": 0}

    def fake_write(stage, *_args):
        (stage / "verified-marker").write_text("complete\n", encoding="utf-8")

    monkeypatch.setattr(prepare, "scan_source", fake_scan)
    monkeypatch.setattr(prepare, "choose_candidates", lambda candidates: candidates)
    monkeypatch.setattr(prepare, "compact_corpus", fake_compact)
    monkeypatch.setattr(prepare, "write_candidate_outputs", fake_write)
    monkeypatch.setattr(prepare, "CURATED_SPECS", ())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_crag_text_global",
            "--source",
            str(source),
            "--output-dir",
            str(output),
            "--expected-source-sha256",
            expected,
        ],
    )

    prepare.main()

    assert (output / "verified-marker").read_text(encoding="utf-8") == "complete\n"
    assert not list(output.parent.glob(f".{output.name}-*"))


@pytest.mark.parametrize("suffix", [(), ("nested-rebuild",)])
def test_main_refuses_the_committed_reviewed_dataset(tmp_path, monkeypatch, suffix) -> None:
    source = tmp_path / "source.jsonl.bz2"
    source.write_bytes(b"synthetic pinned source")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_crag_text_global",
            "--source",
            str(source),
            "--output-dir",
            str(prepare.COMMITTED_DATASET_DIR.joinpath(*suffix)),
            "--expected-source-sha256",
            expected,
        ],
    )

    with pytest.raises(SystemExit, match="refusing to replace the reviewed"):
        prepare.main()
