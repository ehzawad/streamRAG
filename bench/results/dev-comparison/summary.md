# Development comparison — non-final

> Uses checksummed development questions only. The unseen test split was not run.

Configuration: `gpt-5.6-sol` at answer `medium`, trigger `low`, and summary `low` reasoning with `text-embedding-3-large`.

| Path | Accuracy | Support+citation | Median TTFT | Median total | Model calls | Controllers | Retrievals | Dynamic function tools | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| naive | 100.0% | 100.0% | 6199 ms | 6846 ms | 8 | 5 | 5 | 0 | ≥$0.0664 |
| stream | 100.0% | 100.0% | 4942 ms | 5662 ms | 19 | 21 | 12 | 0 | ≥$0.1085 |

## Paired outcome

- Stream TTFT wins: 100.0%
- Median Stream minus Naive TTFT: -3090 ms
- Median Stream minus Naive total: -1251 ms
- Mean Stream minus Naive accuracy: 0.0 percentage points
- Measured wall time: 191.7 s

Dynamic function tools means model-issued `search_local_crag` calls after the
shared primary retrieval. Controller and retrieval calls are reported separately.
A ≥ cost is an observed lower bound because cancelled/timed-out requests do not
return provider usage and are deliberately never estimated as zero.

## Stabilization slices

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Accuracy delta |
|---|---:|---:|---:|---:|
| early_stabilization | 3 | 100.0% | -3129 ms | 0.0 pp |
| late_stabilization | 1 | 100.0% | -1159 ms | 0.0 pp |
| revision_or_ambiguity | 1 | 100.0% | -3090 ms | 0.0 pp |
