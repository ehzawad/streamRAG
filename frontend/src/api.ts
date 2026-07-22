import {
  COMMIT_REQUEST_TIMEOUT_MS,
  SNAPSHOT_REQUEST_TIMEOUT_MS,
  SSE_EVENT_TYPES,
  type AnswerPath,
} from "./runLifecycle.ts";

export type PathName = AnswerPath | "compare";

export type Actor = "trigger_model" | "coordinator" | "answer_pipeline";

export type Grounding = {
  schema_version?: number;
  status:
    | "cites_supplied_crag"
    | "cites_supplied_crag_with_unmatched_markers"
    | "only_unmatched_markers"
    | "supplied_crag_not_cited"
    | "no_crag_supplied";
  has_citation_to_supplied_chunk: boolean;
  citation_marker_occurrences: number;
  cited_chunk_ids: string[];
  matched_chunk_ids: string[];
  unmatched_chunk_ids: string[];
  supplied_pre_retrieved_chunk_ids: string[];
  supplied_tool_chunk_ids: string[];
  matched_pre_retrieved_chunk_ids?: string[];
  matched_tool_chunk_ids?: string[];
  unmatched_seen_in_retained_history_chunk_ids?: string[];
  unmatched_not_seen_in_retained_history_chunk_ids?: string[];
  evidence_token_estimate: { pre_retrieved: number; tool_returned: number; total: number };
  search_local_crag: { attempts: number; completed: number; returned_chunk_count: number };
};

// Only `status` is guaranteed: persistence_failed / persistence_timeout /
// unknown payloads are status-only, and summary_timeout carries no after-fields.
export type ContextCompaction = {
  status:
    | "compressed"
    | "not_needed"
    | "summary_timeout"
    | "persistence_failed"
    | "persistence_timeout"
    | "unknown";
  reason?: string;
  message_count_before?: number;
  message_count_after?: number;
  messages_compacted?: number;
  context_tokens_before?: number;
  context_tokens_after?: number;
  summary_tokens_before?: number;
  summary_tokens_after?: number;
  summary_chars_after?: number;
  summary_elapsed_ms?: number;
  compression_calls?: number;
  history_token_budget?: number;
  history_keep_turns?: number;
};

export type AnswerCommit = {
  branch: "exact_evidence" | "inflight_wait_promoted" | "fallback" | "committed_text";
  fallback_reason: string | null;
  state_at_commit: Record<string, unknown> | null;
  inflight_wait: Record<string, unknown> | null;
};

export type BackendEvent = {
  type: string;
  run_id?: string;
  path?: AnswerPath;
  text?: string;
  answer?: string;
  action?: string;
  state?: string;
  query?: string | null;
  message?: string;
  sources?: Source[];
  // SSE channel + entity envelope (present on turn-channel events, and sequence on all).
  sequence?: number;
  seq?: number;
  actor?: Actor;
  turn_id?: string;
  revision?: number;
  chars?: number;
  append_only?: boolean;
  trigger_id?: string;
  retrieval_id?: string;
  evidence_id?: string;
  origin?: "trigger" | "raw_prefix" | "settled_exact" | "direct_commit";
  reason?: string;
  candidate_query?: string | null;
  candidate_query_compatible?: boolean;
  search_query?: string;
  from_revision?: number;
  to_revision?: number;
  hits?: number;
  elapsed_ms?: number;
  cache_hit?: boolean;
  search_cache_hit?: boolean;
  replacement_retrieval_id?: string;
  // Run-channel enrichments.
  accepted_query?: string;
  evidence_origin?: string;
  grounding?: Grounding;
  commit?: AnswerCommit;
  // Enriched agent.context_compressed fields.
  message_count_before?: number;
  message_count_after?: number;
  messages_compacted?: number;
  context_tokens_before?: number;
  context_tokens_after?: number;
  summary_tokens_after?: number;
  summary_elapsed_ms?: number;
  compression_calls?: number;
  timing?: {
    submit_to_first_token_ms: number | null;
    total_response_ms: number;
    accepted_retrieval_lead_at_commit_ms: number;
    accepted_candidate_retrieval_lead_ms: number;
  };
  retrieval?: {
    cache_hit: boolean;
    calls: number;
  };
  controller?: {
    calls: number;
  };
  reuse?: {
    mode:
      | "precommit_exact"
      | "precommit_revalidated"
      | "presubmit_retrieval_revalidated_at_commit"
      | "inflight_completed_postcommit"
      | "committed_text_retrieval";
    commit_fallbacks: number;
  };
  tool_traces?: unknown[];
  ready_before_commit?: boolean;
  retrieval_completed_before_commit?: boolean;
  candidate?: boolean;
  commit_safe_exact?: boolean;
  estimated_cost_usd?: { total: number; accounting_complete: boolean };
  persistence?: {
    status: string;
    elapsed_ms: number | null;
    context_compaction?: ContextCompaction;
  };
};

export type Source = {
  chunk_id: string;
  title: string;
  url: string;
  score: number;
};

export type ServiceHealth = {
  ok: boolean;
  implementation: AnswerPath;
  metrics_contract_version: number;
  supports_snapshots: boolean;
  dataset_status: string;
  indexed_chunks: number;
  indexed_desired_chunks: number;
  index_ready: boolean;
  dataset_checksums_valid: boolean;
  index_matches_current_corpus: boolean;
  model: string;
  embedding_model: string;
  reasoning_effort: string;
  trigger_reasoning_effort?: string;
  summary_reasoning_effort: string;
  settled_draft_delay_ms?: number;
  service_tier: string;
  instance_id: string;
};

export type ServiceDataStatus = {
  implementation: AnswerPath;
  metrics_contract_version: number;
  supports_snapshots: boolean;
  approval_status: string;
  indexed_chunks: number;
  indexed_desired_chunks: number;
  index_checksum: string;
  index_source_sha256: string;
  current_index_source_sha256: string;
  index_matches_current_corpus: boolean;
  model: string;
  embedding_model: string;
  reasoning_effort: string;
  trigger_reasoning_effort?: string;
  summary_reasoning_effort: string;
  settled_draft_delay_ms?: number;
  service_tier: string;
  configuration: Record<string, unknown>;
  config_hash: string;
  serving_dataset_checksum: string;
  documents_sha256: string;
};

export type ServiceProbe = {
  health: ServiceHealth;
  data: ServiceDataStatus;
};

export type ServiceTopology = {
  services: Partial<Record<AnswerPath, ServiceProbe>>;
  errors: Partial<Record<AnswerPath, string>>;
  comparisonError: string | null;
};

const SERVICE_URLS: Record<AnswerPath, string> = {
  naive: "/api/naive",
  stream: "/api/stream",
};

export const METRICS_CONTRACT_VERSION = 1;
const TOPOLOGY_REQUEST_TIMEOUT_MS = 5_000;

const COMMON_IDENTITY_FIELDS = [
  "config_hash",
  "serving_dataset_checksum",
  "documents_sha256",
  "index_checksum",
  "index_source_sha256",
  "current_index_source_sha256",
  "indexed_chunks",
  "indexed_desired_chunks",
  "model",
  "embedding_model",
  "reasoning_effort",
  "summary_reasoning_effort",
  "service_tier",
  "configuration",
] as const satisfies readonly (keyof ServiceDataStatus)[];

export function serviceBaseUrl(implementation: AnswerPath): string {
  return SERVICE_URLS[implementation];
}

async function fetchJson<T>(
  implementation: AnswerPath,
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timeout = globalThis.setTimeout(abort, TOPOLOGY_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${serviceBaseUrl(implementation)}${path}`, {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`${implementation} ${path} failed: ${response.status}`);
    return response.json() as Promise<T>;
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) {
      throw new Error(`${implementation} ${path} timed out`);
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

async function post<T>(
  implementation: AnswerPath,
  path: string,
  body: unknown,
  signal?: AbortSignal,
  timeoutMs = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timeout = globalThis.setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(`${serviceBaseUrl(implementation)}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`${implementation} ${response.status}: ${detail}`);
    }
    return response.json() as Promise<T>;
  } finally {
    globalThis.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

function assertServiceContract(
  implementation: AnswerPath,
  health: ServiceHealth,
  data: ServiceDataStatus,
): void {
  if (health.implementation !== implementation || data.implementation !== implementation) {
    throw new Error(`${implementation} URL returned the wrong implementation role`);
  }
  if (
    health.metrics_contract_version !== METRICS_CONTRACT_VERSION ||
    data.metrics_contract_version !== METRICS_CONTRACT_VERSION
  ) {
    throw new Error(`${implementation} uses an incompatible metrics contract`);
  }
  const expectedSnapshots = implementation === "stream";
  if (
    health.supports_snapshots !== expectedSnapshots ||
    data.supports_snapshots !== expectedSnapshots
  ) {
    throw new Error(`${implementation} reports an invalid snapshot capability`);
  }
  const triggerFields = [
    health.trigger_reasoning_effort,
    health.settled_draft_delay_ms,
    data.trigger_reasoning_effort,
    data.settled_draft_delay_ms,
  ];
  if (implementation === "stream" && triggerFields.some((value) => value === undefined)) {
    throw new Error("stream does not advertise its typed-input configuration");
  }
  if (implementation === "naive" && triggerFields.some((value) => value !== undefined)) {
    throw new Error("naive advertises Stream-only configuration");
  }
}

export async function probeService(
  implementation: AnswerPath,
  signal?: AbortSignal,
): Promise<ServiceProbe> {
  const [health, data] = await Promise.all([
    fetchJson<ServiceHealth>(implementation, "/v1/health", signal),
    fetchJson<ServiceDataStatus>(implementation, "/v1/data/status", signal),
  ]);
  assertServiceContract(implementation, health, data);
  return { health, data };
}

function comparableValue(value: unknown): string {
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function validateCommonIdentity(
  naive: ServiceDataStatus,
  stream: ServiceDataStatus,
): void {
  for (const field of COMMON_IDENTITY_FIELDS) {
    const naiveValue = comparableValue(naive[field]);
    const streamValue = comparableValue(stream[field]);
    if (!naiveValue || naiveValue === "undefined" || naiveValue !== streamValue) {
      throw new Error(`service identity mismatch: ${field}`);
    }
  }
}

export function validateServiceIsolation(
  naive: ServiceProbe,
  stream: ServiceProbe,
  naiveUrl = serviceBaseUrl("naive"),
  streamUrl = serviceBaseUrl("stream"),
): void {
  const baseUrl = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  const normalizedNaiveUrl = new URL(naiveUrl, baseUrl).href.replace(/\/$/, "");
  const normalizedStreamUrl = new URL(streamUrl, baseUrl).href.replace(/\/$/, "");
  if (normalizedNaiveUrl === normalizedStreamUrl) {
    throw new Error("Naive and Stream must use distinct service URLs");
  }
  const naiveInstance = naive.health.instance_id.trim();
  const streamInstance = stream.health.instance_id.trim();
  if (!naiveInstance || !streamInstance) {
    throw new Error("both services must advertise a non-empty instance ID");
  }
  if (naiveInstance === streamInstance) {
    throw new Error("Naive and Stream must use distinct service instances");
  }
}

export async function getServiceTopology(
  implementations: readonly AnswerPath[] = ["naive", "stream"],
  signal?: AbortSignal,
): Promise<ServiceTopology> {
  const settled = await Promise.allSettled(
    implementations.map((implementation) => probeService(implementation, signal)),
  );
  const services: Partial<Record<AnswerPath, ServiceProbe>> = {};
  const errors: Partial<Record<AnswerPath, string>> = {};

  settled.forEach((result, index) => {
    const implementation = implementations[index];
    if (result.status === "fulfilled") services[implementation] = result.value;
    else errors[implementation] =
      result.reason instanceof Error ? result.reason.message : String(result.reason);
  });

  let comparisonError: string | null = null;
  if (implementations.length === 2 && services.naive && services.stream) {
    try {
      validateServiceIsolation(services.naive, services.stream);
      validateCommonIdentity(services.naive.data, services.stream.data);
    } catch (error) {
      comparisonError = error instanceof Error ? error.message : String(error);
    }
  } else if (implementations.length === 2) {
    comparisonError = "both isolated services are required for comparison";
  }
  return { services, errors, comparisonError };
}

export function sendSnapshot(args: {
  turnId: string;
  sessionId: string;
  revision: number;
  text: string;
  signal?: AbortSignal;
}) {
  return post<{ turn_id: string; revision: number; events_url: string }>(
    "stream",
    `/v1/turns/${args.turnId}/snapshots`,
    {
      session_id: args.sessionId,
      revision: args.revision,
      text: args.text,
    },
    args.signal,
    SNAPSHOT_REQUEST_TIMEOUT_MS,
  );
}

export async function commit(args: {
  implementation: AnswerPath;
  turnId: string;
  sessionId: string;
  revision: number;
  text: string;
  queryTime: string;
  signal?: AbortSignal;
}) {
  const accepted = await post<{
    run_id: string;
    turn_id: string;
    path: AnswerPath;
    events_url: string;
  }>(
    args.implementation,
    `/v1/turns/${args.turnId}/commit`,
    {
      session_id: args.sessionId,
      revision: args.revision,
      text: args.text,
      query_time: args.queryTime,
    },
    args.signal,
    COMMIT_REQUEST_TIMEOUT_MS,
  );
  if (accepted.path !== args.implementation) {
    throw new Error(`${args.implementation} commit returned path ${accepted.path}`);
  }
  return accepted;
}

export async function cancelTurn(implementation: AnswerPath, turnId: string) {
  const response = await fetch(
    `${serviceBaseUrl(implementation)}/v1/turns/${turnId}`,
    { method: "DELETE", keepalive: true },
  );
  if (!response.ok && response.status !== 404) {
    throw new Error(`${implementation} turn cancellation failed: ${response.status}`);
  }
}

export function subscribe(
  implementation: AnswerPath,
  path: string,
  onEvent: (event: BackendEvent) => void,
  onTransportError?: () => void,
) {
  const source = new EventSource(`${serviceBaseUrl(implementation)}${path}`);
  const dispatch = (data: string) => {
    const event = JSON.parse(data) as BackendEvent;
    onEvent({ ...event, path: implementation });
  };
  source.onerror = () => onTransportError?.();
  source.onmessage = (event) => dispatch(event.data);
  SSE_EVENT_TYPES.forEach((name) =>
    source.addEventListener(name, (event) => dispatch((event as MessageEvent).data)),
  );
  return source;
}
