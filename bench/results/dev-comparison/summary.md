# Development comparison — non-final

> Uses checksummed development questions only. The unseen test split was not run.

Configuration: `gpt-5.6-sol` at answer `medium`, trigger `low`, and summary `low` reasoning with `text-embedding-3-large`.

| Path | Accuracy | Support+citation | Median TTFT | Median total | Model calls | Controllers | Retrievals | Dynamic function tools | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| naive | 100.0% | 100.0% | 5907 ms | 6290 ms | 7 | 5 | 5 | 0 | ≥$0.0614 |
| stream | 100.0% | 100.0% | 4575 ms | 5084 ms | 20 | 23 | 12 | 0 | ≥$0.1142 |

## Paired outcome

- Stream TTFT wins: 80.0%
- Median Stream minus Naive TTFT: -629 ms
- Median Stream minus Naive total: -702 ms
- Mean Stream minus Naive accuracy: 0.0 percentage points
- Measured wall time: 180.9 s

Dynamic function tools means model-issued `search_local_crag` calls after the
shared primary retrieval. Controller and retrieval calls are reported separately.
A ≥ cost is an observed lower bound because cancelled/timed-out requests do not
return provider usage and are deliberately never estimated as zero.

## Stabilization slices

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Accuracy delta |
|---|---:|---:|---:|---:|
| early_stabilization | 3 | 100.0% | -3104 ms | 0.0 pp |
| late_stabilization | 1 | 0.0% | 464 ms | 0.0 pp |
| revision_or_ambiguity | 1 | 100.0% | -479 ms | 0.0 pp |
