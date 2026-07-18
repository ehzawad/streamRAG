# Dataset and knowledge base

**Status:** `candidate_pending_human_review`. The final unseen benchmark is
blocked until a human freezes the dataset.

| Item | Value |
|---|---|
| Source | Pinned CRAG Task 1/2 development release |
| Knowledge base | 250 complete cleaned documents |
| Vector index | 1,000 local Qdrant points |
| Embeddings | `text-embedding-3-large`, 3,072 dimensions |
| Chunking | 400 tokens with 50-token overlap |
| Development set | 5 visible questions |
| Final set | 10 sealed, unseen questions |

The committed corpus is [`data/crag_eval`](../data/crag_eval). Documents are
cleaned but never shortened. Fifteen audited evidence documents cover the 15
questions; 235 deterministic distractors make retrieval non-trivial.

## Keeping the test set unseen

Services index only `documents.jsonl.bz2`. Documents contain no question, answer,
alias, split, or gold fields. Test answers and support IDs stay in
`test_gold.jsonl`, which the application and benchmark runner do not read.

For the final run, the tooling creates a redacted inference bundle, records
content-addressed predictions, and only then gives gold to the offline scorer.
The runner refuses candidate status, leaked gold, fingerprint drift, shared
service identity, or incomplete outputs.

## Verify and index

```bash
make verify-data
```

Verification checks the manifest, all bound files, 250 documents, and exactly
1,000 deterministic chunks. Index sync embeds those verified bytes with the real
OpenAI API.

Each service can build its own index through `/v1/data/sync`. For a faster fair
comparison, the provisioner builds one stopped seed index and clones it into
separate Naive and Stream stores. The running services share no mutable vector,
database, cache, or session state.

## Human approval

Review all 15 rows in
[`data/crag_eval/REVIEW_SHEET.md`](../data/crag_eval/REVIEW_SHEET.md). Approval
changes the status to `approved_frozen` and regenerates checksums. It approves the
evaluation data only—not the implementation or result.

Until then, development checks require `ALLOW_UNREVIEWED_DATASET=1`; final
commands fail closed.

## Optional source audit

Normal reproduction uses the committed corpus. To audit it against the pinned
705 MiB upstream release:

```bash
make crag-source
uv run python -m scripts.prepare_crag_text_global \
  --source data/raw/crag_official/crag_task_1_and_2_dev_v5.jsonl.bz2 \
  --output-dir tmp/crag_eval-reproduced \
  --min-words 8
diff -u data/crag_eval/checksums.sha256 \
  tmp/crag_eval-reproduced/checksums.sha256
```
