# Canonical CRAG text evaluation dataset

There is exactly one dataset: [`data/crag_eval`](../data/crag_eval). Its recorded
status is `candidate_pending_human_review`.

Development questions may be used for implementation checks. The 10 unseen test
questions and scorer-only gold must not be used by either path until a human
reviewer approves the sheet and freezes the status/checksums. No final unseen
benchmark is currently claimed.

## Fixed shape

- 5 development questions and 10 unseen test questions
- 250 complete cleaned CRAG documents
- 15 audited evidence documents, including contradiction evidence for the
  false-premise development control
- 235 deterministic full-document distractors
- exactly 1,000 chunks/Qdrant points with the shared 400-token, 50-token-overlap
  chunker
- all five CRAG domains in development and two test questions per domain
- no managed Qdrant URL, account, or API key

The 1,000-point shape is an assessment runtime choice, not a Qdrant limit. Pages
are cleaned of markup, scripts, and styles but never character- or token-truncated.

## Selection and leakage boundary

Question/support mappings are explicit and checked against the pinned upstream
checksum, split/domain metadata, evidence phrases, and support-page chunk counts.
Selection does not inspect Naive or Stream answers, latency, cost, or rank.

Retrievable documents contain no query, answer, alias, split, or gold wrapper
fields. Test answers and supporting IDs exist only in `test_gold.jsonl`; services
index only `documents.jsonl.bz2`. Final evaluation first creates a redacted
inference bundle, serves both paths from that bundle, generates content-addressed
predictions, and only then gives gold to the offline scorer.

Each question has a provisional `early_stabilization`, `late_stabilization`, or
`revision_or_ambiguity` label. These low-confidence labels were assigned without
using A/B outcomes and remain subject to human review. They are interpretation
strata, not permission to remove inconvenient questions.

## Verification and indexing

The checksum-bound compressed corpus is committed, so normal reproduction does
not download the 705 MiB upstream release:

```bash
make verify-data
```

Verification parses the manifest once, hashes each bound file once, loads all 250
complete documents, and deterministically rechunks them to exactly 1,000 points.
Index sync then embeds the captured corpus bytes with the configured real OpenAI
embedding API.

Each API supports independent `/v1/data/sync`. The comparison provisioner uses a
faster but equally isolated route: it builds one temporary real index, stops the
seed service, verifies quiescence, and clones Qdrant plus matching SQLite index
metadata into separate Naive and Stream state directories. The services do not
share files after startup and neither can warm the other's cache or session.

Sync marks durable state unready before mutation and ready only after source,
checksum, index-pipeline version, desired count, and physical point count agree.
A failed sync remains unavailable across restart. Answers recheck approval,
checksums, source/version/count identity, and physical count. Active turns block
maintenance; admitted maintenance blocks new turns.

## Optional provenance regeneration

Downloading and regenerating from the pinned official release is an audit path,
not part of normal reviewer reproduction:

```bash
make crag-source
uv run python -m scripts.prepare_crag_text_global \
  --source data/raw/crag_official/crag_task_1_and_2_dev_v5.jsonl.bz2 \
  --output-dir tmp/crag_eval-reproduced \
  --min-words 8
diff -u data/crag_eval/checksums.sha256 \
  tmp/crag_eval-reproduced/checksums.sha256
```

The downloader verifies the pinned SHA-256 before use. Regeneration is not needed
to run either app or the benchmark.

## Approval gate

Review all 15 rows in
[`data/crag_eval/REVIEW_SHEET.md`](../data/crag_eval/REVIEW_SHEET.md), including
wording, expected answers/aliases, timestamps, evidence, split role, and
stabilization label. Approval means deliberately changing the status to
`approved_frozen` and regenerating its checksums. It approves only the evaluation
set—not the implementation or any result.

Until then, the final inference-bundle builder and launcher fail closed.
`ALLOW_UNREVIEWED_DATASET=1` is only for explicitly non-reportable development
checks.
