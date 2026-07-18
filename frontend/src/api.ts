export type PathName = "naive" | "stream" | "compare";

export type BackendEvent = {
  type: string;
  path?: "naive" | "stream";
  text?: string;
  answer?: string;
  action?: string;
  query?: string | null;
  message?: string;
  sources?: Source[];
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
      | "commit_endpoint";
    commit_fallbacks: number;
  };
  tool_traces?: unknown[];
  ready_before_commit?: boolean;
  retrieval_completed_before_commit?: boolean;
  estimated_cost_usd?: { total: number; accounting_complete: boolean };
};

export type Source = {
  chunk_id: string;
  title: string;
  url: string;
  score: number;
};

const DEFAULT_API_URL = `${window.location.protocol}//${window.location.hostname}:8000`;
const API_URL = (import.meta.env.VITE_API_URL || DEFAULT_API_URL).replace(/\/$/, "");

async function post<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
  timeoutMs = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timeout = window.setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`${response.status}: ${detail}`);
    }
    return response.json() as Promise<T>;
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export async function getHealth() {
  const response = await fetch(`${API_URL}/v1/health`);
  if (!response.ok) throw new Error(`Health check failed: ${response.status}`);
  return response.json() as Promise<{
    ok: boolean;
    dataset_status: string;
    indexed_chunks: number;
    indexed_desired_chunks: number;
    index_ready: boolean;
    dataset_checksums_valid: boolean;
    index_matches_current_corpus: boolean;
    model: string;
    reasoning_effort: string;
    trigger_reasoning_effort: string;
    summary_reasoning_effort: string;
  }>;
}

export function sendSnapshot(args: {
  turnId: string;
  sessionId: string;
  path: "stream" | "compare";
  revision: number;
  text: string;
  signal?: AbortSignal;
}) {
  return post(`${`/v1/turns/${args.turnId}`}/snapshots`, {
    session_id: args.sessionId,
    path: args.path,
    revision: args.revision,
    text: args.text,
    client_ts_ms: performance.now(),
  }, args.signal, 5_000);
}

export function commit(args: {
  turnId: string;
  sessionId: string;
  path: PathName;
  revision: number;
  text: string;
  signal?: AbortSignal;
}) {
  return post<{ run_id: string; events_url: string }>(
    `/v1/turns/${args.turnId}/commit`,
    {
      session_id: args.sessionId,
      path: args.path,
      revision: args.revision,
      text: args.text,
      query_time: new Date().toISOString(),
      client_ts_ms: performance.now(),
    },
    args.signal,
  );
}

export async function cancelTurn(turnId: string) {
  const response = await fetch(`${API_URL}/v1/turns/${turnId}`, {
    method: "DELETE",
    keepalive: true,
  });
  if (!response.ok && response.status !== 404) {
    throw new Error(`Turn cancellation failed: ${response.status}`);
  }
}

export function subscribe(
  path: string,
  onEvent: (event: BackendEvent) => void,
  onTransportError?: () => void,
) {
  const source = new EventSource(`${API_URL}${path}`);
  source.onerror = () => onTransportError?.();
  source.onmessage = (event) => onEvent(JSON.parse(event.data) as BackendEvent);
  [
    "input.ack",
    "trigger.decision",
    "trigger.error",
    "retrieval.started",
    "retrieval.ready",
    "retrieval.discarded",
    "retrieval.revalidated",
    "retrieval.reused",
    "retrieval.fallback",
    "retrieval.error",
    "answer.started",
    "answer.delta",
    "answer.completed",
    "answer.error",
    "agent.tool_started",
    "agent.tool_completed",
    "agent.context_compressed",
    "run.started",
    "run.completed",
    "run.error",
  ].forEach((name) =>
    source.addEventListener(name, (event) =>
      onEvent(JSON.parse((event as MessageEvent).data) as BackendEvent),
    ),
  );
  return source;
}
