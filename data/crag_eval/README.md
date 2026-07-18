# CRAG text evaluation dataset

> **Status: PENDING HUMAN REVIEW.** This output is not frozen and must not be used
> for a final unseen benchmark yet.

This candidate contains 5 development and 10 test questions,
matching the assessment's guidance that ten to twenty fixed test queries is plenty.
All questions retrieve over one deduplicated global corpus of
250 pages derived from the supplied official CRAG JSONL. Every
included page keeps its complete cleaned text. One concise, manually audited evidence page
is retained for every question, including contradiction evidence for the false-premise
control; a fixed-hash distractor sample fills the corpus. If needed,
the largest sampled distractors are replaced with smaller complete pages solely to meet
the assignment runtime budget; no page is cut. The resulting
1,000 chunks stay at or below the
1,000-point embedded-Qdrant target and require no Qdrant API key.

The construction is text-specific: approximate standard five-character WPM typing,
sample cumulative dirty text every 400 ms (partial words included) only at ticks strictly
before **Send**, carry full text in the higher-revision commit, and exclude speech-only
latency gains.

The official Task 1/2 release contains up to five pages per query. Therefore this script's
global aggregate is larger and more distractor-rich than CRAG-200, but it does **not**
reproduce the Stream RAG paper's separately described 100,000-document corpus or BGE
reranking stack.

Test labels are stored only in `test_gold.jsonl`; the application indexes only
the checksum-bound `documents.jsonl.bz2` corpus. Selection is the fixed manual mapping
in the preparation script. Its exact support IDs and evidence phrases are validated
against the pinned source, and neither implementation's outputs are consulted.

Scorer-only gold rows freeze the audited `supporting_doc_ids`. Human review may
add genuinely supporting pages to `acceptable_supporting_doc_ids`; citation syntax
alone is never reported as grounded correctness.

Each selected query receives an `early_stabilization`, `late_stabilization`, or
`revision_or_ambiguity` candidate label only after selection. These transparent
lexical/question-type heuristics are pending manual review and must not be used to
cherry-pick questions based on benchmark outcomes.

The heuristic follows the entity/constraint-position hypothesis in
<https://arxiv.org/abs/2606.20113>, not a simple-complex type ranking. That paper
finds comparison and aggregation can stabilize early, set questions are the clearest
late extreme, and question type explains only a small share of variation. All labels
therefore have low confidence; measured prefix retrieval traces are authoritative.
