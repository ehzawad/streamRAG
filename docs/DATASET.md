# Canonical CRAG text evaluation dataset

There is exactly one evaluation dataset:
[`data/crag_eval`](../data/crag_eval). Its recorded status is
`candidate_pending_human_review`.

Development questions may be used for implementation and prompt checks. The 10
unseen test questions and scorer-only gold must not be used by either path until a
human reviewer approves the sheet and freezes the checksums. The unseen split has
not been run.

## Fixed shape

- 5 development questions and 10 unseen test questions from official CRAG Task
  1/2 material
- 250 complete cleaned source documents
- 15 manually audited evidence documents, including contradiction evidence for
  the false-premise development control
- 235 deterministic full-document distractors
- exactly 1,000 chunks/local-Qdrant points using the shared 400-token, 50-token-
  overlap chunker
- all five CRAG domains in development and two test questions per domain
- no Qdrant URL, account, or API key

The 1,000-point target is an assessment runtime choice, not a Qdrant product
limit. The generator fails closed if the selected complete pages exceed it.
Selected pages are cleaned of markup, script, and style content, but page text is
never character- or token-truncated.

A measured clean index used `text-embedding-3-large` at 3,072 dimensions and
produced 1,000/1,000 points from 366,142 provider-counted embedding tokens in
40.94 s. At the price recorded for that run, the index cost was $0.04759846.

## Selection and leakage boundary

Question/support mappings are explicit in the generator. Each mapping is checked
against the pinned source checksum, interaction role/domain/split, evidence phrases,
and a maximum support-page chunk count. Selection does not inspect Naive or Stream
outputs, latency, rank, or cost.

Retrievable documents contain no query, answer, alias, split, or gold wrapper
fields. Test answers and supporting IDs remain in scorer-only `test_gold.jsonl`;
the application indexes only `documents.jsonl.bz2`. The formal benchmark first
builds a redacted inference bundle, then serves two isolated indexes from that
bundle. The offline scorer receives gold only after predictions exist.

Each query has a candidate `early_stabilization`, `late_stabilization`, or
`revision_or_ambiguity` label. These labels were assigned independently of A/B
outcomes, have low confidence, and remain part of the human review. They are
strata for interpretation, not permission to discard inconvenient questions.

## Normal reproduction

The checksum-bound compressed corpus is committed, so normal reproduction does
not download the 705 MiB upstream CRAG archive:

```bash
make verify-data
```

Verification checks the manifest, loads all 250 complete documents, and exactly
rechunks them to 1,000 points. Indexing then uses the real configured OpenAI
embedding API. On the acceptance machine, dataset verification was sub-second and
indexing took about 41 seconds.

## Optional provenance regeneration

Downloading and regenerating from the pinned official release is an audit path,
not part of the 15–20 minute reviewer workflow:

```bash
make crag-source
uv run python scripts/prepare_crag_text_global.py \
  --source data/raw/crag_official/crag_task_1_and_2_dev_v5.jsonl.bz2 \
  --output-dir tmp/crag_eval-reproduced \
  --dev-questions 5 \
  --test-questions 10 \
  --min-words 8
diff -u data/crag_eval/checksums.sha256 \
  tmp/crag_eval-reproduced/checksums.sha256
```

The source downloader checks SHA-256 before use. Regeneration from the pinned
705 MiB source took 109.0 s on the acceptance machine.

## Approval gate

Review all 15 rows in
[`data/crag_eval/REVIEW_SHEET.md`](../data/crag_eval/REVIEW_SHEET.md), including
wording, expected answers/aliases, query time, evidence, split role, and
stabilization label. Approval means changing the status to `approved_frozen` and
regenerating its checksums before building the two formal indexes. It does not
approve the implementation or benchmark result.

Until that explicit approval, the final launcher and inference-bundle builder
fail closed. `ALLOW_UNREVIEWED_DATASET=1` exists only for clearly labeled
development checks.
