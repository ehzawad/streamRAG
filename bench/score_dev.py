#!/usr/bin/env python3
"""Score the checksummed development comparison without touching unseen test gold."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from bench.score import annotate, paired_summary, path_summary, read_jsonl, sha256_file
except ModuleNotFoundError:  # Direct execution places bench/ on sys.path.
    from score import annotate, paired_summary, path_summary, read_jsonl, sha256_file

ROOT = Path(__file__).resolve().parents[1]


def stabilization_summaries(
    annotated: list[dict[str, Any]],
    gold_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    labels = sorted({str(row.get("stabilization_class") or "unreviewed") for row in annotated})
    for label in labels:
        rows = [row for row in annotated if row.get("stabilization_class") == label]
        by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_path[str(row["path"])].append(row)
        path_metrics = {
            path: path_summary(items, gold_by_id) for path, items in sorted(by_path.items())
        }
        paired = paired_summary(rows)
        summaries[label] = {
            "query_count": len({str(row["id"]) for row in rows}),
            "paths": {
                path: {
                    "expected_answer_accuracy": metrics["expected_answer_accuracy"],
                    "supported_expected_answer_rate": metrics[
                        "supported_expected_answer_rate"
                    ],
                    "median_ttft_ms": metrics["median_ttft_ms"],
                    "median_total_ms": metrics["median_total_ms"],
                    "mean_cost_usd": metrics["mean_cost_usd"],
                }
                for path, metrics in path_metrics.items()
            },
            "paired": {
                "stream_ttft_win_rate": paired["stream_ttft_win_rate"],
                "median_stream_minus_naive_ttft_ms": paired[
                    "median_stream_minus_naive_ttft_ms"
                ],
                "median_stream_minus_naive_ttft_percent": paired[
                    "median_stream_minus_naive_ttft_percent"
                ],
                "median_stream_minus_naive_total_ms": paired[
                    "median_stream_minus_naive_total_ms"
                ],
                "mean_stream_minus_naive_accuracy": paired[
                    "mean_stream_minus_naive_accuracy"
                ],
            },
        }
    return summaries


def validate_dev_run(
    rows: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    predictions: Path,
) -> dict[str, Any]:
    manifest_path = predictions.with_suffix(".manifest.json")
    issues: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "issues": [f"dev manifest unavailable: {exc}"]}
    if manifest.get("smoke_non_reportable") is not True:
        issues.append("run is not explicitly marked development-only/non-reportable")
    if (
        manifest.get("finalized") is not True
        or manifest.get("run_status") != "completed_non_reportable"
    ):
        issues.append("development run did not complete cleanly")
    manifest_queries = manifest_path.parent / str(manifest.get("queries") or "")
    if manifest.get("queries_sha256") != sha256_file(manifest_queries):
        issues.append("manifest query checksum does not match its query file")
    if manifest.get("queries_sha256") != sha256_file(Path(dev_rows[0]["_source_path"])):
        issues.append("predictions were not generated from the selected development file")
    selected_ids = [str(row["id"]) for row in dev_rows]
    observed = [
        (str(row.get("id")), int(row.get("repetition") or 0), str(row.get("path")))
        for row in rows
    ]
    counts = Counter(observed)
    expected = {(query_id, 1, path) for query_id in selected_ids for path in ("naive", "stream")}
    if set(observed) != expected or any(value != 1 for value in counts.values()):
        issues.append("predictions are not exactly one Naive and one Stream run per dev query")
    if any("error" in row for row in rows):
        issues.append("one or more development outputs failed")
    return {
        "status": "complete" if not issues else "invalid",
        "issues": issues,
        "manifest": manifest,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Development comparison — non-final",
        "",
        "> Uses checksummed development questions only. The unseen test split was not run.",
        "",
        f"Configuration: `{summary['configuration']['model']}` at "
        f"answer `{summary['configuration']['reasoning_effort']}`, trigger "
        f"`{summary['configuration']['trigger_reasoning_effort']}`, and summary "
        f"`{summary['configuration']['summary_reasoning_effort']}` reasoning with "
        f"`{summary['configuration']['embedding_model']}`.",
        "",
        "| Path | Accuracy | Support+citation | Median TTFT | Median total | "
        "Model calls | Controllers | Retrievals | Dynamic function tools | Cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for path, values in summary["paths"].items():
        cost_prefix = "" if values["cost_metric_status"] == "complete" else "≥"
        lines.append(
            f"| {path} | {values['expected_answer_accuracy'] * 100:.1f}% | "
            f"{values['supported_expected_answer_rate'] * 100:.1f}% | "
            f"{values['median_ttft_ms']:.0f} ms | {values['median_total_ms']:.0f} ms | "
            f"{values['total_model_api_calls']:.0f} | {values['total_controller_calls']} | "
            f"{values['total_retrieval_calls']} | {values['total_dynamic_tool_calls']} | "
            f"{cost_prefix}${values['observed_cost_usd']:.4f} |"
        )
    paired = summary["paired"]
    lines.extend(
        [
            "",
            "## Paired outcome",
            "",
            f"- Stream TTFT wins: {paired['stream_ttft_win_rate'] * 100:.1f}%",
            f"- Median Stream minus Naive TTFT: "
            f"{paired['median_stream_minus_naive_ttft_ms']:.0f} ms",
            f"- Median Stream minus Naive total: "
            f"{paired['median_stream_minus_naive_total_ms']:.0f} ms",
            f"- Mean Stream minus Naive accuracy: "
            f"{paired['mean_stream_minus_naive_accuracy'] * 100:.1f} percentage points",
            f"- Measured wall time: {summary['measured_wall_s']:.1f} s",
            "",
            "Dynamic function tools means model-issued `search_local_crag` calls after the",
            "shared primary retrieval. Controller and retrieval calls are reported separately.",
            "A ≥ cost is an observed lower bound because cancelled/timed-out requests do not",
            "return provider usage and are deliberately never estimated as zero.",
        ]
    )
    lines.extend(
        [
            "",
            "## Stabilization slices",
            "",
            "| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | "
            "Accuracy delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, values in summary["stabilization_slices"].items():
        paired = values["paired"]
        lines.append(
            f"| {label} | {values['query_count']} | "
            f"{paired['stream_ttft_win_rate'] * 100:.1f}% | "
            f"{paired['median_stream_minus_naive_ttft_ms']:.0f} ms | "
            f"{paired['mean_stream_minus_naive_accuracy'] * 100:.1f} pp |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a non-final development A/B run")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_dev = read_jsonl(args.dev)
    manifest_path = args.predictions.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected_count = int(manifest.get("query_count") or 0)
    dev_rows = [
        dict(row, _source_path=str(args.dev.resolve()))
        for row in source_dev[:selected_count]
    ]
    rows = read_jsonl(args.predictions)
    integrity = validate_dev_run(rows, dev_rows, args.predictions)
    if integrity["status"] != "complete":
        raise SystemExit("development run integrity failed: " + "; ".join(integrity["issues"]))
    gold_by_id = {str(row["id"]): row for row in dev_rows}
    annotated = annotate(rows, gold_by_id)
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in annotated:
        by_path[str(row["path"])].append(row)
    statuses = manifest["data_status"]
    identity = statuses["naive"]
    summary = {
        "schema_version": 1,
        "status": "development_only_non_final",
        "unseen_test_run": False,
        "query_count": selected_count,
        "path_runs": len(rows),
        "measured_wall_s": float(manifest["completed_unix_s"] - manifest["created_unix_s"]),
        "configuration": {
            "model": identity["model"],
            "reasoning_effort": identity["reasoning_effort"],
            "trigger_reasoning_effort": identity.get(
                "trigger_reasoning_effort", identity["reasoning_effort"]
            ),
            "summary_reasoning_effort": identity.get(
                "summary_reasoning_effort", identity["reasoning_effort"]
            ),
            "embedding_model": identity["embedding_model"],
            "indexed_chunks": identity["indexed_chunks"],
        },
        "paths": {
            path: path_summary(items, gold_by_id) for path, items in sorted(by_path.items())
        },
        "paired": paired_summary(annotated),
        "stabilization_slices": stabilization_summaries(annotated, gold_by_id),
        "cost_metric_status": (
            "complete"
            if all(
                row.get("estimated_cost_usd", {}).get("accounting_complete") is True
                for row in rows
            )
            else "observed_lower_bound_non_final"
        ),
        "integrity": {"status": "complete", "issues": []},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
