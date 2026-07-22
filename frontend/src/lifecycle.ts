import type { AnswerPath } from "./runLifecycle.ts";
import type { BackendEvent, Grounding } from "./api.ts";

// Event-sourced, entity-keyed lifecycle state. Each incoming SSE event is
// normalized with its subscription context and folded into per-(path, turn)
// buckets. Retrievals own an evidence sub-state and never reopen once terminal.

export type Channel = "turn" | "run";

export type EventContext = {
  path: AnswerPath;
  channel: Channel;
  turnKey: string;
  scopeId: string;
  runId?: string;
};

export type RetrievalState =
  | "retrieving"
  | "ready"
  | "accepted"
  | "cancelled"
  | "superseded"
  | "discarded"
  | "error";

export type EvidenceState =
  | "none"
  | "candidate"
  | "ready"
  | "promoted"
  | "accepted"
  | "invalidated";

const TERMINAL_RETRIEVAL: ReadonlySet<RetrievalState> = new Set<RetrievalState>([
  "accepted",
  "cancelled",
  "superseded",
  "discarded",
  "error",
]);

const MAX_BUCKETS = 4; // active turn plus up to three completed turns.
const MAX_TRANSITIONS = 400; // per bucket; oldest dropped, counted in droppedTransitions.
const MAX_DIAGNOSTICS = 100;
const MAX_SEEN_KEYS = 4096; // dedupe map safety valve for pathological turn lengths.
const MAX_COUNTED_RUNS = 64;

type TriggerEntity = {
  id: string;
  label: string;
  num: number;
  state: "pending" | "decided" | "error" | "cancelled" | "discarded";
  action: string | null;
  query: string | null;
  candidateQuery: string | null;
  candidateCompatible: boolean | null;
  revision: number | null;
  reason: string | null;
  actor: string | null;
  elapsedMs: number | null;
};

type RetrievalEntity = {
  id: string;
  label: string;
  num: number;
  state: RetrievalState;
  evidence: EvidenceState;
  query: string | null;
  origin: string | null;
  revision: number | null;
  fromRevision: number | null;
  toRevision: number | null;
  hits: number | null;
  cacheHit: boolean | null;
  candidate: boolean | null;
  commitSafeExact: boolean | null;
  reason: string | null;
  replacementId: string | null;
  actor: string | null;
};

export type Transition = {
  sequence: number | null;
  at: number;
  type: string;
  channel: Channel;
  actor: string | null;
  entityId: string | null;
  entityLabel: string | null;
  query: string | null;
  revision: number | null;
  reason: string | null;
  extra: Record<string, unknown>;
};

type Diagnostic = {
  at: number;
  type: string;
  entityId: string | null;
  message: string;
};

type TurnBucket = {
  turnKey: string;
  path: AnswerPath;
  createdAt: number;
  firstEventAt: number | null;
  order: number;
  seen: Record<string, true>;
  triggers: Record<string, TriggerEntity>;
  retrievals: Record<string, RetrievalEntity>;
  triggerCount: number;
  retrievalCount: number;
  transitions: Transition[];
  droppedTransitions: number;
  diagnostics: Diagnostic[];
  grounding: Grounding | null;
};

export type CompactionSummary = {
  status: string;
  before: number | null;
  after: number | null;
};

type PathState = {
  turns: TurnBucket[];
  orderCounter: number;
  compaction: {
    count: number;
    last: CompactionSummary | null;
    countedRuns: Record<string, true>;
  };
};

export type LifecycleState = {
  paths: Record<AnswerPath, PathState>;
};

export type LifecycleAction =
  | { kind: "event"; event: BackendEvent; ctx: EventContext }
  | { kind: "reset" };

function emptyPathState(): PathState {
  return {
    turns: [],
    orderCounter: 0,
    compaction: { count: 0, last: null, countedRuns: {} },
  };
}

export function initialLifecycleState(): LifecycleState {
  return {
    paths: {
      naive: emptyPathState(),
      stream: emptyPathState(),
    },
  };
}

function numOrNull(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function createBucket(turnKey: string, path: AnswerPath, at: number, order: number): TurnBucket {
  return {
    turnKey,
    path,
    createdAt: at,
    firstEventAt: null,
    order,
    seen: {},
    triggers: {},
    retrievals: {},
    triggerCount: 0,
    retrievalCount: 0,
    transitions: [],
    droppedTransitions: 0,
    diagnostics: [],
    grounding: null,
  };
}

// Shallow-clone every container the caller might mutate so the previous state
// object graph is never touched (keeps the reducer pure under StrictMode).
function cloneBucket(bucket: TurnBucket): TurnBucket {
  return {
    ...bucket,
    seen: { ...bucket.seen },
    triggers: { ...bucket.triggers },
    retrievals: { ...bucket.retrievals },
    transitions: bucket.transitions.slice(),
    diagnostics: bucket.diagnostics.slice(),
  };
}

function upsertTrigger(bucket: TurnBucket, id: string | undefined): TriggerEntity {
  const key = id ?? "trigger-unknown";
  const existing = bucket.triggers[key];
  if (existing) {
    const copy = { ...existing };
    bucket.triggers[key] = copy;
    return copy;
  }
  bucket.triggerCount += 1;
  const created: TriggerEntity = {
    id: key,
    label: `trigger-${bucket.triggerCount}`,
    num: bucket.triggerCount,
    state: "pending",
    action: null,
    query: null,
    candidateQuery: null,
    candidateCompatible: null,
    revision: null,
    reason: null,
    actor: null,
    elapsedMs: null,
  };
  bucket.triggers[key] = created;
  return created;
}

function upsertRetrieval(bucket: TurnBucket, id: string | undefined): RetrievalEntity {
  const key = id ?? "retrieval-unknown";
  const existing = bucket.retrievals[key];
  if (existing) {
    const copy = { ...existing };
    bucket.retrievals[key] = copy;
    return copy;
  }
  bucket.retrievalCount += 1;
  const created: RetrievalEntity = {
    id: key,
    label: `retrieval-${bucket.retrievalCount}`,
    num: bucket.retrievalCount,
    state: "retrieving",
    evidence: "none",
    query: null,
    origin: null,
    revision: null,
    fromRevision: null,
    toRevision: null,
    hits: null,
    cacheHit: null,
    candidate: null,
    commitSafeExact: null,
    reason: null,
    replacementId: null,
    actor: null,
  };
  bucket.retrievals[key] = created;
  return created;
}

function recordDiagnostic(bucket: TurnBucket, event: BackendEvent, at: number, message: string) {
  if (bucket.diagnostics.length >= MAX_DIAGNOSTICS) bucket.diagnostics.shift();
  bucket.diagnostics.push({
    at,
    type: event.type,
    entityId: event.retrieval_id ?? event.evidence_id ?? event.trigger_id ?? null,
    message,
  });
}

// Returns true when the retrieval is already terminal (transition blocked).
function guardTerminal(
  bucket: TurnBucket,
  retrieval: RetrievalEntity,
  event: BackendEvent,
  at: number,
): boolean {
  if (TERMINAL_RETRIEVAL.has(retrieval.state)) {
    recordDiagnostic(
      bucket,
      event,
      at,
      `${event.type} ignored: ${retrieval.label} already ${retrieval.state}`,
    );
    return true;
  }
  return false;
}

function processEvent(
  bucket: TurnBucket,
  event: BackendEvent,
  ctx: EventContext,
  at: number,
  seqVal: number | null,
): Transition {
  const actor = strOrNull(event.actor);
  const revision = numOrNull(event.revision);
  const base: Transition = {
    sequence: seqVal,
    at,
    type: event.type,
    channel: ctx.channel,
    actor,
    entityId: null,
    entityLabel: null,
    query: strOrNull(event.query),
    revision,
    reason: strOrNull(event.reason),
    extra: {},
  };

  switch (event.type) {
    case "input.ack": {
      base.extra = {
        analyzer: event.analyzer ?? null,
        chars: numOrNull(event.chars),
        append_only: boolOrNull(event.append_only),
      };
      return base;
    }
    case "snapshot.coalesced": {
      base.reason = strOrNull(event.reason);
      base.extra = { reason: event.reason ?? null, trigger_id: event.trigger_id ?? null };
      return base;
    }
    case "trigger.decision": {
      const trigger = upsertTrigger(bucket, event.trigger_id);
      trigger.state = "decided";
      trigger.action = strOrNull(event.action);
      trigger.query = strOrNull(event.query);
      trigger.candidateQuery = strOrNull(event.candidate_query);
      trigger.candidateCompatible = boolOrNull(event.candidate_query_compatible);
      trigger.revision = revision;
      trigger.actor = actor;
      trigger.elapsedMs = numOrNull(event.elapsed_ms);
      base.entityId = trigger.id;
      base.entityLabel = trigger.label;
      base.extra = {
        action: event.action ?? null,
        candidate_query: event.candidate_query ?? null,
        candidate_query_compatible: boolOrNull(event.candidate_query_compatible),
        elapsed_ms: numOrNull(event.elapsed_ms),
      };
      return base;
    }
    case "trigger.error": {
      const trigger = upsertTrigger(bucket, event.trigger_id);
      trigger.state = "error";
      trigger.reason = strOrNull(event.reason);
      trigger.revision = revision;
      base.entityId = trigger.id;
      base.entityLabel = trigger.label;
      return base;
    }
    case "trigger.cancelled": {
      const trigger = upsertTrigger(bucket, event.trigger_id);
      trigger.state = "cancelled";
      trigger.reason = strOrNull(event.reason);
      base.entityId = trigger.id;
      base.entityLabel = trigger.label;
      return base;
    }
    case "trigger.discarded": {
      const trigger = upsertTrigger(bucket, event.trigger_id);
      trigger.state = "discarded";
      trigger.reason = strOrNull(event.reason);
      base.entityId = trigger.id;
      base.entityLabel = trigger.label;
      base.extra = { reason: event.reason ?? null };
      return base;
    }
    case "retrieval.started": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      if (!guardTerminal(bucket, retrieval, event, at)) {
        retrieval.state = "retrieving";
        retrieval.evidence = event.candidate ? "candidate" : "none";
        retrieval.query = strOrNull(event.query);
        retrieval.origin = strOrNull(event.origin);
        retrieval.revision = revision;
        retrieval.candidate = boolOrNull(event.candidate);
        retrieval.commitSafeExact = boolOrNull(event.commit_safe_exact);
        retrieval.actor = actor;
      }
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.extra = {
        origin: event.origin ?? null,
        candidate: boolOrNull(event.candidate),
        commit_safe_exact: boolOrNull(event.commit_safe_exact),
      };
      return base;
    }
    case "retrieval.ready": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      if (!guardTerminal(bucket, retrieval, event, at)) {
        retrieval.state = "ready";
        retrieval.evidence = retrieval.evidence === "promoted" ? "promoted" : "ready";
        retrieval.hits = numOrNull(event.hits);
        retrieval.cacheHit = boolOrNull(event.search_cache_hit ?? event.cache_hit);
        retrieval.commitSafeExact = boolOrNull(event.commit_safe_exact);
        retrieval.candidate = boolOrNull(event.candidate);
        retrieval.origin = strOrNull(event.origin) ?? retrieval.origin;
        retrieval.query = strOrNull(event.query) ?? retrieval.query;
        retrieval.revision = revision ?? retrieval.revision;
      }
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.extra = {
        origin: event.origin ?? null,
        hits: numOrNull(event.hits),
        elapsed_ms: numOrNull(event.elapsed_ms),
        candidate: boolOrNull(event.candidate),
        commit_safe_exact: boolOrNull(event.commit_safe_exact),
        cache_hit: boolOrNull(event.search_cache_hit ?? event.cache_hit),
      };
      return base;
    }
    case "retrieval.error": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      if (!guardTerminal(bucket, retrieval, event, at)) {
        retrieval.state = "error";
        retrieval.reason = strOrNull(event.reason);
      }
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      return base;
    }
    case "retrieval.cancelled": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      const replacementLabel = event.replacement_retrieval_id
        ? upsertRetrieval(bucket, event.replacement_retrieval_id).label
        : null;
      if (!guardTerminal(bucket, retrieval, event, at)) {
        retrieval.state = event.replacement_retrieval_id ? "superseded" : "cancelled";
        retrieval.reason = strOrNull(event.reason);
        retrieval.replacementId = strOrNull(event.replacement_retrieval_id);
        retrieval.query = strOrNull(event.query) ?? retrieval.query;
      }
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.extra = {
        reason: event.reason ?? null,
        replacement_retrieval_id: event.replacement_retrieval_id ?? null,
        replacement_label: replacementLabel,
      };
      return base;
    }
    case "retrieval.kept": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      retrieval.reason = strOrNull(event.reason);
      retrieval.query = strOrNull(event.query) ?? retrieval.query;
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.extra = { reason: event.reason ?? null };
      return base;
    }
    case "retrieval.discarded": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      if (!guardTerminal(bucket, retrieval, event, at)) {
        retrieval.state = "discarded";
        retrieval.reason = strOrNull(event.reason);
        retrieval.revision = revision ?? retrieval.revision;
      }
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      return base;
    }
    case "retrieval.revalidated": {
      // Same retrieved evidence promoted to cover a newer draft revision.
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      if (!TERMINAL_RETRIEVAL.has(retrieval.state)) {
        retrieval.evidence = "promoted";
        retrieval.fromRevision = numOrNull(event.from_revision);
        retrieval.toRevision = numOrNull(event.to_revision);
        retrieval.revision = numOrNull(event.to_revision) ?? retrieval.revision;
        retrieval.reason = strOrNull(event.reason);
        retrieval.query = strOrNull(event.query) ?? retrieval.query;
      } else {
        recordDiagnostic(bucket, event, at, `revalidate ignored: ${retrieval.label} already ${retrieval.state}`);
      }
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.reason = strOrNull(event.reason);
      base.extra = {
        from_revision: numOrNull(event.from_revision),
        to_revision: numOrNull(event.to_revision),
        state: event.state ?? null,
      };
      return base;
    }
    case "evidence.invalidated": {
      // evidence_id equals the producing retrieval_id. Invalidated evidence
      // also ends the retrieval's useful life, so terminalize it — otherwise
      // the activity strip keeps reporting the stale "ready" state.
      const retrieval = upsertRetrieval(bucket, event.evidence_id);
      retrieval.evidence = "invalidated";
      retrieval.reason = strOrNull(event.reason);
      if (!TERMINAL_RETRIEVAL.has(retrieval.state)) retrieval.state = "discarded";
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.query = strOrNull(event.query) ?? base.query;
      base.extra = { evidence_id: event.evidence_id ?? null, reason: event.reason ?? null };
      return base;
    }
    case "draft.settled": {
      if (event.retrieval_id) {
        const retrieval = upsertRetrieval(bucket, event.retrieval_id);
        // A settled draft with state "ready" upgrades existing evidence to
        // commit-safe-exact server-side without another retrieval.ready.
        if (event.state === "ready" && !TERMINAL_RETRIEVAL.has(retrieval.state)) {
          retrieval.commitSafeExact = true;
          if (retrieval.evidence === "none" || retrieval.evidence === "candidate") {
            retrieval.evidence = "ready";
          }
        }
        base.entityId = retrieval.id;
        base.entityLabel = retrieval.label;
      }
      base.extra = { state: event.state ?? null, retrieval_id: event.retrieval_id ?? null };
      return base;
    }
    case "retrieval.reused": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      // The accepted evidence at commit; overrides prior non-accept states.
      retrieval.state = "accepted";
      retrieval.evidence = "accepted";
      retrieval.origin = strOrNull(event.origin) ?? retrieval.origin;
      retrieval.query = strOrNull(event.query) ?? retrieval.query;
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.extra = {
        origin: event.origin ?? null,
        ready_before_commit: boolOrNull(event.ready_before_commit),
        retrieval_completed_before_commit: boolOrNull(event.retrieval_completed_before_commit),
      };
      return base;
    }
    case "retrieval.fallback": {
      const retrieval = upsertRetrieval(bucket, event.retrieval_id);
      retrieval.reason = strOrNull(event.reason);
      retrieval.query = strOrNull(event.query) ?? retrieval.query;
      base.entityId = retrieval.id;
      base.entityLabel = retrieval.label;
      base.extra = { search_query: event.search_query ?? null, reason: event.reason ?? null };
      return base;
    }
    case "run.started": {
      base.extra = { run_id: event.run_id ?? null };
      return base;
    }
    case "answer.started": {
      // Generation is starting, so retrieval work is settled. The fallback
      // direct-commit retrieval emits no ready/reused event of its own; sweep
      // remaining non-terminal retrievals so nothing stays "live" forever:
      // the newest one produced the accepted evidence, the rest lost.
      Object.values(bucket.retrievals)
        .filter((entity) => !TERMINAL_RETRIEVAL.has(entity.state))
        .sort((left, right) => right.num - left.num)
        .forEach((entity, index) => {
          const copy = { ...entity };
          if (index === 0) {
            copy.state = "accepted";
            if (copy.evidence !== "invalidated") copy.evidence = "accepted";
          } else {
            copy.state = "superseded";
          }
          bucket.retrievals[copy.id] = copy;
        });
      base.query = strOrNull(event.accepted_query) ?? base.query;
      base.extra = {
        accepted_query: event.accepted_query ?? null,
        evidence_origin: event.evidence_origin ?? null,
      };
      return base;
    }
    case "answer.ready": {
      if (event.grounding) bucket.grounding = event.grounding;
      base.extra = { grounding_status: event.grounding?.status ?? null };
      return base;
    }
    case "answer.completed": {
      if (event.grounding) bucket.grounding = event.grounding;
      base.extra = {
        grounding_status: event.grounding?.status ?? null,
        commit_branch: event.commit?.branch ?? null,
        fallback_reason: event.commit?.fallback_reason ?? null,
      };
      return base;
    }
    case "answer.error":
    case "run.error": {
      // A failure before answer.started (e.g. committed-text retrieval timeout
      // after retrieval.fallback) leaves retrievals non-terminal; close them so
      // the activity strip cannot keep claiming retrieval is active.
      Object.values(bucket.retrievals)
        .filter((entity) => !TERMINAL_RETRIEVAL.has(entity.state))
        .forEach((entity) => {
          bucket.retrievals[entity.id] = { ...entity, state: "error", reason: "run_failed" };
        });
      base.reason = strOrNull(event.message) ?? base.reason;
      base.extra = { message: event.message ?? null };
      return base;
    }
    case "agent.tool_started": {
      base.query = strOrNull(event.query) ?? base.query;
      base.extra = { query: event.query ?? null };
      return base;
    }
    case "agent.context_compressed": {
      base.reason = strOrNull(event.reason) ?? base.reason;
      base.extra = {
        message_count_before: numOrNull(event.message_count_before),
        message_count_after: numOrNull(event.message_count_after),
        messages_compacted: numOrNull(event.messages_compacted),
        context_tokens_before: numOrNull(event.context_tokens_before),
        context_tokens_after: numOrNull(event.context_tokens_after),
      };
      return base;
    }
    default:
      return base;
  }
}

function updateCompaction(pathState: PathState, event: BackendEvent, ctx: EventContext): PathState["compaction"] {
  const current = pathState.compaction;
  const runId = ctx.runId ?? (ctx.channel === "run" ? ctx.scopeId : undefined);

  if (event.type === "agent.context_compressed") {
    if (runId && current.countedRuns[runId]) return current;
    const countedRuns =
      Object.keys(current.countedRuns).length >= MAX_COUNTED_RUNS ? {} : current.countedRuns;
    return {
      count: current.count + 1,
      last: {
        status: "compressed",
        before: numOrNull(event.message_count_before),
        after: numOrNull(event.message_count_after),
      },
      countedRuns: runId ? { ...countedRuns, [runId]: true } : countedRuns,
    };
  }

  if (event.type === "answer.completed" && event.persistence?.context_compaction?.status === "compressed") {
    const compaction = event.persistence.context_compaction;
    const summary: CompactionSummary = {
      status: "compressed",
      before: compaction.message_count_before ?? null,
      after: compaction.message_count_after ?? null,
    };
    if (runId && current.countedRuns[runId]) {
      return { ...current, last: summary };
    }
    return {
      count: current.count + 1,
      last: summary,
      countedRuns: runId ? { ...current.countedRuns, [runId]: true } : current.countedRuns,
    };
  }

  return current;
}

function applyEvent(state: LifecycleState, event: BackendEvent, ctx: EventContext): LifecycleState {
  // Streaming answer tokens carry no lifecycle value.
  if (event.type === "answer.delta") return state;

  const pathState = state.paths[ctx.path];
  const seqVal = event.sequence ?? event.seq ?? null;
  const existingIndex = pathState.turns.findIndex((turn) => turn.turnKey === ctx.turnKey);
  const existing = existingIndex >= 0 ? pathState.turns[existingIndex] : null;

  const dedupeKey =
    seqVal == null ? null : `${ctx.path}:${ctx.channel}:${ctx.scopeId}:${seqVal}`;
  if (existing && dedupeKey && existing.seen[dedupeKey]) return state;

  const at = Date.now();
  let orderCounter = pathState.orderCounter;
  let bucket: TurnBucket;
  if (existing) {
    bucket = cloneBucket(existing);
  } else {
    orderCounter += 1;
    bucket = createBucket(ctx.turnKey, ctx.path, at, orderCounter);
  }
  if (bucket.firstEventAt == null) bucket.firstEventAt = at;
  if (dedupeKey) {
    if (Object.keys(bucket.seen).length >= MAX_SEEN_KEYS) bucket.seen = {};
    bucket.seen[dedupeKey] = true;
  }

  const transition = processEvent(bucket, event, ctx, at, seqVal);
  if (bucket.transitions.length >= MAX_TRANSITIONS) {
    bucket.transitions.shift();
    bucket.droppedTransitions += 1;
  }
  bucket.transitions.push(transition);

  let turns: TurnBucket[];
  if (existing) {
    turns = pathState.turns.slice();
    turns[existingIndex] = bucket;
  } else {
    turns = [...pathState.turns, bucket];
  }
  // Bound completed-turn history; the active (newest) turn is always retained.
  if (turns.length > MAX_BUCKETS) {
    turns = turns.slice(turns.length - MAX_BUCKETS);
  }

  const compaction = updateCompaction(pathState, event, ctx);
  const nextPathState: PathState = { turns, orderCounter, compaction };
  return { paths: { ...state.paths, [ctx.path]: nextPathState } };
}

export function lifecycleReducer(state: LifecycleState, action: LifecycleAction): LifecycleState {
  if (action.kind === "reset") return initialLifecycleState();
  return applyEvent(state, action.event, action.ctx);
}

// ---- Selectors -----------------------------------------------------------

function activeBucket(state: LifecycleState, path: AnswerPath): TurnBucket | null {
  const turns = state.paths[path].turns;
  return turns.length ? turns[turns.length - 1] : null;
}

export type CurrentActivity = {
  query: string | null;
  state: string;
  actor: string | null;
  reason: string | null;
  origin: string | null;
  fromRevision: number | null;
  toRevision: number | null;
};

function idleActivity(): CurrentActivity {
  return {
    query: null,
    state: "idle",
    actor: null,
    reason: null,
    origin: null,
    fromRevision: null,
    toRevision: null,
  };
}

function activityFromTransition(transition: Transition): CurrentActivity {
  const typeMap: Record<string, string> = {
    "retrieval.fallback": "fallback",
    "retrieval.cancelled": "cancelled",
    "retrieval.discarded": "discarded",
    "retrieval.error": "error",
    "retrieval.revalidated": "promoted",
    "retrieval.reused": "ready",
    "evidence.invalidated": "discarded",
  };
  let stateToken = typeMap[transition.type] ?? "idle";
  if (transition.type === "draft.settled") {
    stateToken = transition.extra.state === "ready" ? "exact_ready" : "retrieving";
  }
  if (transition.type === "retrieval.ready") {
    stateToken = transition.extra.commit_safe_exact ? "exact_ready" : "ready";
  }
  if (transition.type === "retrieval.cancelled" && transition.extra.replacement_retrieval_id) {
    stateToken = "superseded";
  }
  return {
    query: transition.query,
    state: stateToken,
    actor: transition.actor,
    reason: transition.reason,
    origin: (transition.extra.origin as string | null) ?? null,
    fromRevision: (transition.extra.from_revision as number | null) ?? null,
    toRevision: (transition.extra.to_revision as number | null) ?? null,
  };
}

// Newest non-terminal retrieval, else the last terminal transition.
export function currentActivity(state: LifecycleState, path: AnswerPath): CurrentActivity {
  const bucket = activeBucket(state, path);
  if (!bucket) return idleActivity();

  const live = Object.values(bucket.retrievals)
    .filter((retrieval) => !TERMINAL_RETRIEVAL.has(retrieval.state))
    .sort((left, right) => right.num - left.num)[0];

  if (live) {
    let stateToken: string;
    if (live.state === "retrieving") stateToken = "retrieving";
    else if (live.evidence === "promoted") stateToken = "promoted";
    else if (live.commitSafeExact) stateToken = "exact_ready";
    else stateToken = "ready";
    return {
      query: live.query,
      state: stateToken,
      actor: live.actor,
      reason: live.reason,
      origin: live.origin,
      fromRevision: live.fromRevision,
      toRevision: live.toRevision,
    };
  }

  const last = bucket.transitions[bucket.transitions.length - 1];
  return last ? activityFromTransition(last) : idleActivity();
}

export type TimelineRow = Transition & { turnKey: string; offsetMs: number };

// Ordered transitions across retained turns (oldest first, newest last).
export function timeline(state: LifecycleState, path: AnswerPath): TimelineRow[] {
  const rows: TimelineRow[] = [];
  for (const bucket of state.paths[path].turns) {
    const base = bucket.firstEventAt ?? bucket.createdAt;
    for (const transition of bucket.transitions) {
      rows.push({ ...transition, turnKey: bucket.turnKey, offsetMs: transition.at - base });
    }
  }
  return rows;
}

// Transitions dropped from retained buckets by the per-bucket cap.
export function timelineOverflow(state: LifecycleState, path: AnswerPath): number {
  return state.paths[path].turns.reduce((total, bucket) => total + bucket.droppedTransitions, 0);
}

export function groundingFor(
  state: LifecycleState,
  turnKey: string,
  path: AnswerPath,
): Grounding | null {
  const bucket = state.paths[path].turns.find((turn) => turn.turnKey === turnKey);
  return bucket?.grounding ?? null;
}

export function compactionFor(
  state: LifecycleState,
  path: AnswerPath,
): { count: number; last: CompactionSummary | null } {
  const compaction = state.paths[path].compaction;
  return { count: compaction.count, last: compaction.last };
}
