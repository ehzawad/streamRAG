# Development comparison — non-final

> Uses checksummed development questions only. The unseen test split was not run.

Configuration: `gpt-5.6-sol` at answer `medium`, trigger `low`, and summary `low` reasoning with `text-embedding-3-large`.

| Path | Accuracy | Support+citation | Median TTFT | Median total | Model calls | Controllers | Retrievals | Dynamic function tools | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| naive | 100.0% | 100.0% | 5342 ms | 5944 ms | 9 | 5 | 5 | 0 | ≥$0.0668 |
| stream | 100.0% | 100.0% | 6276 ms | 6913 ms | 22 | 23 | 5 | 0 | ≥$0.1018 |

## Paired outcome

- Stream TTFT wins: 60.0%
- Median Stream minus Naive TTFT: -336 ms
- Median Stream minus Naive total: -358 ms
- Mean Stream minus Naive accuracy: 0.0 percentage points
- Measured wall time: 182.4 s

Dynamic function tools means model-issued `search_local_crag` calls after the
shared primary retrieval. Controller and retrieval calls are reported separately.
A ≥ cost is an observed lower bound because cancelled/timed-out requests do not
return provider usage and are deliberately never estimated as zero.

## Stabilization slices

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Accuracy delta |
|---|---:|---:|---:|---:|
| early_stabilization | 3 | 100.0% | -2055 ms | 0.0 pp |
| late_stabilization | 1 | 0.0% | 2256 ms | 0.0 pp |
| revision_or_ambiguity | 1 | 0.0% | 2173 ms | 0.0 pp |
