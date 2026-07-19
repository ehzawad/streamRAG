# Dataset and human review

## Current status

`candidate_pending_human_review` is a metadata value, not a function or file.
Its authoritative location is
`data/crag_eval/dataset_summary.json` at
`.selection.approval_status`; the leakage audit records the same state. The APIs
and benchmark runner read this value and refuse final evaluation until it becomes
`approved_frozen` through an explicit human-authorized freeze.

| Item | Value |
|---|---|
| Source | pinned CRAG Task 1/2 development release |
| Knowledge base | 250 complete cleaned documents |
| Vector index | exactly 1,000 Qdrant points per path |
| Chunking | 400 tokens with 50-token overlap |
| Embeddings | `text-embedding-3-large`, 3,072 dimensions |
| Development questions | 5 visible rows |
| Final questions | 10 sealed rows |
| Approval | pending human review |

## What is embedded

The only source collection sent to the embedding API is:

[`data/crag_eval/documents.jsonl.bz2`](../data/crag_eval/documents.jsonl.bz2)

It is compressed JSON Lines: 250 lines, one complete document per line. Each row
contains `doc_id`, `title`, `url`, `text`, `domain`, source timestamps, a snippet,
and a content checksum. Questions, answers, aliases, split labels, and gold are
not stored in those rows.

Inspect the corpus without modifying it:

```bash
# Confirm the document count.
bzcat data/crag_eval/documents.jsonl.bz2 | wc -l

# Inspect rows interactively.
bzcat data/crag_eval/documents.jsonl.bz2 | jq -c . | less

# Inspect one audited support document.
bzcat data/crag_eval/documents.jsonl.bz2 \
  | jq -c 'select(.doc_id == "crag-global-9d22ffbe3ca22f9a9858bd6e")'
```

`shared/data/crag.py` verifies and loads these rows. `chunk_documents` performs
the deterministic split, and each API's `/v1/data/sync` endpoint embeds and
upserts the resulting 1,000 chunks into its own Qdrant service.

## File roles

| File | Consumer |
|---|---|
| `documents.jsonl.bz2` | both indexers; the vector-search knowledge base |
| `dev_queries.jsonl` | visible development runner |
| `test_queries.jsonl` | final runner after approval |
| `test_gold.jsonl` | offline scorer only; never an API or inference input |
| `dataset_summary.json` | counts, selection method, input contract, approval state |
| `selection_manifest.json` | deterministic source selection and provenance |
| `leakage_audit.json` | verifies separation between corpus, questions, and gold |
| `REVIEW_SHEET.md` | the human review checklist for all 15 questions |
| `checksums.sha256` | binds every integrity-sensitive dataset file |

## How to review the candidate

Open
[`data/crag_eval/REVIEW_SHEET.md`](../data/crag_eval/REVIEW_SHEET.md). It contains
all 5 development rows and all 10 test rows. Review metadata and source evidence
only; do not run either implementation on the sealed test questions.

For every row:

1. Confirm the wording and time anchor are unambiguous.
2. Confirm the expected answer and aliases.
3. Open the recorded source pages or locate the audited `doc_id` in the committed
   corpus and confirm that it supports the answer or intended abstention.
4. Check that the candidate stabilization class is plausible without looking at
   Naive or StreamRAG outputs.
5. Confirm the development/test split role.
6. Mark all six checkboxes for that row only after those checks pass.

If a row is wrong, correct or replace it before approval and regenerate every
affected checksum. Do not approve around a known defect.

There is intentionally no automatic self-approval command. Completing the sheet
records the review; it does not silently change the status. A separate explicit
approval must update the review state to `approved_frozen`, regenerate
`checksums.sha256`, and commit the complete freeze as one auditable change.

## Integrity and leakage boundary

```bash
make verify-data
```

The verifier checks all nine bound files, 250 source rows, deterministic chunking,
and the 1,000-point target. During final evaluation, tooling creates a redacted
inference bundle containing the corpus and test questions but no gold. Predictions
are finalized and hashed before the offline scorer can read `test_gold.jsonl`.

The final runner rejects candidate status, checksum drift, gold leakage, shared
service identity, incompatible fingerprints, missing outputs, or an invalid
freeze binding.

## Provenance

The corpus keeps complete cleaned pages; documents are never shortened. Fifteen
preselected evidence documents pending human review cover the questions, and 235
deterministic distractors make retrieval non-trivial. Normal reproduction uses
the committed compressed corpus and does not download the 705 MiB upstream
release.

The source is Meta's
[CRAG Task 1/2 development release](https://github.com/facebookresearch/CRAG),
licensed CC BY-NC 4.0. Source IDs and URLs remain in each row for audit and
attribution.
