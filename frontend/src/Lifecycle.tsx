import { useEffect, useRef } from "react";

import type { Grounding, PathName } from "./api.ts";
import {
  compactionFor,
  currentActivity,
  type CurrentActivity,
  type LifecycleState,
  timeline,
  timelineOverflow,
  type TimelineRow,
} from "./lifecycle.ts";
import { selectedImplementations } from "./runLifecycle.ts";

const GROUNDING_DISCLAIMER = "Marker match only; does not verify each claim.";
const TIMELINE_ROW_CAP = 80;

function truncate(text: string, max = 80): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function quoted(text: string): string {
  return `'${truncate(text)}'`;
}

function actorLabel(actor: string | null): string | null {
  if (actor === "trigger_model") return "Trigger model";
  if (actor === "coordinator") return "Coordinator";
  if (actor === "answer_pipeline") return "Answer pipeline";
  return null;
}

function actorClass(actor: string | null): string {
  if (actor === "trigger_model") return "lc-actor trigger";
  if (actor === "coordinator") return "lc-actor coordinator";
  if (actor === "answer_pipeline") return "lc-actor pipeline";
  return "lc-actor system";
}

function actorRowClass(actor: string | null): string {
  if (actor === "trigger_model") return "trigger";
  if (actor === "coordinator") return "coordinator";
  if (actor === "answer_pipeline") return "pipeline";
  return "system";
}

const REASON_LABELS: Record<string, string> = {
  non_append_correction: "non-append correction",
  replacement_query: "replacement query",
  candidate_rejected: "candidate rejected",
  accepted_evidence_won: "accepted evidence won",
  commit_fallback: "commit fallback",
  turn_closed: "turn closed",
  timeout: "timeout",
  failure: "failure",
  same_query_compatible: "same query compatible",
  snapshot_superseded: "snapshot superseded",
  post_commit: "post-commit",
  trigger_in_flight: "trigger in flight",
};

function humanizeReason(reason: string | null): string {
  if (!reason) return "";
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

const ORIGIN_LABELS: Record<string, string> = {
  trigger: "trigger",
  raw_prefix: "raw prefix",
  settled_exact: "settled exact",
  direct_commit: "direct commit",
};

function originLabel(origin: unknown): string {
  return typeof origin === "string" ? ORIGIN_LABELS[origin] ?? origin.replace(/_/g, " ") : "unknown";
}

const GROUNDING_SHORT: Record<Grounding["status"], string> = {
  cites_supplied_crag: "grounded",
  cites_supplied_crag_with_unmatched_markers: "grounded, some unmatched",
  only_unmatched_markers: "citation mismatch",
  supplied_crag_not_cited: "not cited",
  no_crag_supplied: "no CRAG",
};

const COMMIT_BRANCH_LABELS: Record<string, string> = {
  exact_evidence: "exact evidence",
  inflight_wait_promoted: "in-flight promoted",
  fallback: "fallback",
  committed_text: "committed text",
};

function num(value: unknown): string {
  return typeof value === "number" ? String(value) : "?";
}

function humanize(row: TimelineRow): string {
  const label = row.entityLabel ?? "entity";
  const extra = row.extra;
  switch (row.type) {
    case "input.ack":
      return `Typed input acknowledged · ${num(extra.chars)} chars${
        extra.append_only === false ? " · correction" : ""
      }`;
    case "snapshot.coalesced":
      return "Snapshot coalesced";
    case "trigger.decision":
      return `${label} decided: ${extra.action ?? "?"}${row.query ? ` — ${quoted(row.query)}` : ""}`;
    case "trigger.error":
      return `${label} errored`;
    case "trigger.cancelled":
      return `${label} cancelled`;
    case "trigger.discarded":
      return `${label} discarded`;
    case "retrieval.started":
      return `${label} started (${originLabel(extra.origin)})${row.query ? ` — ${quoted(row.query)}` : ""}`;
    case "retrieval.ready":
      return `${label} ready${extra.hits != null ? ` · ${num(extra.hits)} hits` : ""}${extra.commit_safe_exact ? " · exact" : ""}`;
    case "retrieval.error":
      return `${label} errored`;
    case "retrieval.cancelled":
      return extra.replacement_label
        ? `${label} cancelled — replaced by ${extra.replacement_label}`
        : `${label} cancelled`;
    case "retrieval.kept":
      return `${label} kept — same query compatible`;
    case "retrieval.discarded":
      return `${label} discarded`;
    case "retrieval.revalidated":
      return `Same evidence promoted rev ${num(extra.from_revision)} → ${num(extra.to_revision)}`;
    case "evidence.invalidated":
      return "Evidence invalidated";
    case "draft.settled":
      return extra.state === "ready"
        ? "Exact draft evidence ready"
        : "Draft settled — retrieving exact text";
    case "retrieval.reused":
      return `${label} evidence accepted${
        extra.ready_before_commit
          ? " · ready before Send"
          : extra.retrieval_completed_before_commit
            ? " · completed before Send"
            : " · finished after Send"
      }`;
    case "retrieval.fallback":
      return `${label} fallback at Send${extra.search_query ? ` — ${quoted(String(extra.search_query))}` : ""}`;
    case "run.started":
      return "Run started";
    case "answer.started":
      return `Answer generation started${row.query ? ` — ${quoted(row.query)}` : ""}`;
    case "answer.ready":
      return `Answer ready${
        extra.grounding_status ? ` · ${GROUNDING_SHORT[extra.grounding_status as Grounding["status"]] ?? extra.grounding_status}` : ""
      }`;
    case "answer.completed":
      return `Answer completed${
        extra.commit_branch ? ` · ${COMMIT_BRANCH_LABELS[String(extra.commit_branch)] ?? extra.commit_branch}` : ""
      }`;
    case "answer.error":
      return "Answer failed";
    case "run.error":
      return "Run failed";
    case "agent.tool_started":
      return `Tool search started${row.query ? ` — ${quoted(row.query)}` : ""}`;
    case "agent.tool_completed":
      return "Tool search completed";
    case "agent.context_compressed":
      return `Context compressed ${num(extra.message_count_before)}→${num(extra.message_count_after)} msgs`;
    case "run.completed":
      return "Run transport closed";
    default:
      return row.type;
  }
}

type ActivityChip = { label: string; tone: string };

function activityChip(activity: CurrentActivity): ActivityChip {
  switch (activity.state) {
    case "retrieving":
      return { label: "Retrieving…", tone: "wait" };
    case "ready":
      return { label: "Evidence ready", tone: "green" };
    case "exact_ready":
      return { label: "Exact-draft ready", tone: "green" };
    case "promoted":
      return {
        label: `Promoted rev ${activity.fromRevision ?? "?"}→${activity.toRevision ?? "?"}`,
        tone: "green",
      };
    case "superseded":
      return { label: "Superseded", tone: "amber" };
    case "discarded":
      return { label: "Discarded", tone: "amber" };
    case "cancelled":
      return { label: "Cancelled", tone: "amber" };
    case "fallback":
      return { label: "Fallback at Send", tone: "amber" };
    case "error":
      return { label: "Retrieval error", tone: "warn" };
    default:
      return { label: "Waiting for a precise partial query.", tone: "idle" };
  }
}

export function LifecycleActivity({ mode, state }: { mode: PathName; state: LifecycleState }) {
  if (mode === "naive") {
    return (
      <div className="lifecycle-activity" role="status" aria-label="Speculative retrieval activity">
        <span className="lc-note">Naive: retrieval starts at Send.</span>
      </div>
    );
  }
  const activity = currentActivity(state, "stream");
  const chip = activityChip(activity);
  const actor = actorLabel(activity.actor);
  return (
    <div className="lifecycle-activity" role="status" aria-label="Speculative retrieval activity">
      <span className="lc-strip-title">Speculative retrieval</span>
      {actor && <span className={actorClass(activity.actor)}>{actor}</span>}
      <span className={`lc-chip ${chip.tone}`}>{chip.label}</span>
      {activity.query && <code className="lc-query">{truncate(activity.query)}</code>}
      {activity.reason && <span className="lc-reason">{humanizeReason(activity.reason)}</span>}
    </div>
  );
}

function TimelineRowView({ row }: { row: TimelineRow }) {
  const actor = actorLabel(row.actor);
  const showRev = row.revision != null && row.type !== "retrieval.revalidated";
  return (
    <li className={`lc-row ${actorRowClass(row.actor)}`}>
      <span className="lc-time">{(row.offsetMs / 1000).toFixed(1)}s</span>
      {actor && <span className={actorClass(row.actor)}>{actor}</span>}
      <span className="lc-label">{humanize(row)}</span>
      {showRev && <span className="lc-rev">rev {row.revision}</span>}
      {row.reason && <span className="lc-reason">{humanizeReason(row.reason)}</span>}
    </li>
  );
}

function TimelineLane({
  label,
  rows,
  overflow,
}: {
  label: string;
  rows: TimelineRow[];
  overflow: number;
}) {
  const visible = rows.slice(-TIMELINE_ROW_CAP);
  const hidden = rows.length - visible.length + overflow;
  const listRef = useRef<HTMLOListElement | null>(null);

  // Keep the newest rows in view unless the user has scrolled up to read
  // history; new content is appended below the fold otherwise.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const distanceFromBottom = list.scrollHeight - list.clientHeight - list.scrollTop;
    if (distanceFromBottom < 200) list.scrollTop = list.scrollHeight;
  }, [visible.length, rows.length]);

  return (
    <div className="lifecycle-lane">
      <div className="lane-head">{label}</div>
      {hidden > 0 && (
        <div className="lane-hidden">
          {hidden} older row{hidden === 1 ? "" : "s"} hidden
        </div>
      )}
      {visible.length === 0 ? (
        <div className="lane-empty">No lifecycle events yet.</div>
      ) : (
        <ol className="lane-rows" ref={listRef}>
          {visible.map((row, index) => (
            <TimelineRowView key={`${row.turnKey}:${row.type}:${row.sequence ?? "n"}:${index}`} row={row} />
          ))}
        </ol>
      )}
    </div>
  );
}

export function LifecycleTimeline({ mode, state }: { mode: PathName; state: LifecycleState }) {
  return (
    <section className="lifecycle-timeline" aria-label="Retrieval lifecycle">
      <div className="lifecycle-timeline-head">
        <h2>Retrieval lifecycle</h2>
        <span>Entity-keyed transitions per path, newest last.</span>
      </div>
      <div className={`lifecycle-lanes ${mode === "compare" ? "" : "single"}`}>
        {mode !== "stream" && (
          <TimelineLane
            label="Naive"
            rows={timeline(state, "naive")}
            overflow={timelineOverflow(state, "naive")}
          />
        )}
        {mode !== "naive" && (
          <TimelineLane
            label="Stream"
            rows={timeline(state, "stream")}
            overflow={timelineOverflow(state, "stream")}
          />
        )}
      </div>
    </section>
  );
}

type GroundingBadgeInfo = { label: string; tone: string };

function groundingBadge(grounding: Grounding): GroundingBadgeInfo {
  switch (grounding.status) {
    case "cites_supplied_crag":
      return { label: `Cites CRAG evidence · ${grounding.matched_chunk_ids.length} chunks`, tone: "green" };
    case "cites_supplied_crag_with_unmatched_markers":
      return { label: "Cites CRAG · unmatched markers", tone: "amber" };
    case "only_unmatched_markers": {
      const historical = grounding.unmatched_seen_in_retained_history_chunk_ids ?? [];
      const allHistorical =
        historical.length > 0 && historical.length === grounding.unmatched_chunk_ids.length;
      return allHistorical
        ? { label: "Prior-turn citations · not current evidence", tone: "amber" }
        : { label: "Citation mismatch", tone: "warn" };
    }
    case "supplied_crag_not_cited":
      return { label: "Evidence supplied · not cited", tone: "amber" };
    case "no_crag_supplied":
      return { label: "No CRAG evidence this turn", tone: "neutral" };
    default:
      return { label: "Grounding unknown", tone: "neutral" };
  }
}

function groundingTitle(grounding: Grounding): string {
  const matched = grounding.matched_chunk_ids.length ? grounding.matched_chunk_ids.join(", ") : "none";
  const unmatched = grounding.unmatched_chunk_ids.length ? grounding.unmatched_chunk_ids.join(", ") : "none";
  return `Matched: ${matched}\nUnmatched: ${unmatched}\n${GROUNDING_DISCLAIMER}`;
}

export function GroundingBadge({ grounding }: { grounding: Grounding | null }) {
  if (!grounding) return null;
  const badge = groundingBadge(grounding);
  const detail = groundingTitle(grounding);
  return (
    <span
      className={`grounding ${badge.tone}`}
      title={detail}
      tabIndex={0}
      aria-label={`${badge.label}. ${detail.replace(/\n/g, " ")}`}
    >
      {badge.label}
    </span>
  );
}

export function CompactionChips({ mode, state }: { mode: PathName; state: LifecycleState }) {
  const chips = selectedImplementations(mode)
    .map((path) => ({ path, ...compactionFor(state, path) }))
    .filter((entry) => entry.count > 0);
  if (!chips.length) return null;
  return (
    <div className="memory-chips">
      {chips.map((entry) => (
        <span className="memory-chip" key={entry.path}>
          {entry.path === "naive" ? "Naive" : "Stream"} memory: compressed {entry.count}×
        </span>
      ))}
    </div>
  );
}
