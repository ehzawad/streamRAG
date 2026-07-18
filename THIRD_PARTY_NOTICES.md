# Third-party notices

## CRAG dataset

The derived evaluation corpus in `data/crag_eval` comes from the official CRAG
Task 1/2 development release by Meta. The pinned source URL and SHA-256 are recorded
in `data/crag_eval/dataset_summary.json` and the generated checksum manifest.

The upstream CRAG repository identifies the dataset license as **CC BY-NC 4.0**:
<https://github.com/facebookresearch/CRAG>. The included 250-document subset is
therefore for non-commercial assessment/research use under that license. Source
URLs and document identifiers are preserved for attribution and audit. This notice
does not change the upstream license or grant commercial rights.

## Research papers

Copies of the Applied AI Engineer assessment and Stream RAG paper under
`docs/references` are included as task/research references. Copyright remains with
their respective authors and publishers. Their inclusion does not relicense the
documents under this project's terms.

## Software dependencies

Python and frontend dependencies are third-party works installed from their
respective package registries and pinned by `uv.lock` and
`frontend/package-lock.json`. Each dependency remains subject to the license in
its own distribution. No third-party dependency source is vendored in this
repository.
