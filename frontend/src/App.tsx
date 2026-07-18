import { useEffect, useRef, useState } from "react";

import {
  cancelTurn,
  commit,
  getHealth,
  type BackendEvent,
  type PathName,
  sendSnapshot,
  type Source,
  subscribe,
} from "./api";

type Panel = {
  answer: string;
  sources: Source[];
  status: string;
  firstToken: number | null;
  total: number | null;
  cost: number | null;
  accountingComplete: boolean | null;
  retrievalLead: number | null;
  candidateRetrievalLead: number | null;
  reuseMode: string | null;
  cacheHit: boolean | null;
  controllerCalls: number | null;
  retrievalCalls: number | null;
  toolCalls: number | null;
  fallbacks: number | null;
};

type SnapshotJob = {
  text: string;
  path: "stream" | "compare";
  turnId: string;
  revision: number;
  epoch: number;
  signal: AbortSignal;
};

const emptyPanel = (status: string): Panel => ({
  answer: "",
  sources: [],
  status,
  firstToken: null,
  total: null,
  cost: null,
  accountingComplete: null,
  retrievalLead: null,
  candidateRetrievalLead: null,
  reuseMode: null,
  cacheHit: null,
  controllerCalls: null,
  retrievalCalls: null,
  toolCalls: null,
  fallbacks: null,
});

const initialPanels = (mode: PathName): Record<"naive" | "stream", Panel> => ({
  naive: emptyPanel(mode === "stream" ? "Not selected" : "Retrieval begins after Send."),
  stream: emptyPanel(mode === "naive" ? "Not selected" : "Waiting for a precise partial query."),
});

export default function App() {
  const [mode, setMode] = useState<PathName>("compare");
  const [query, setQuery] = useState("");
  const [health, setHealth] = useState("Connecting…");
  const [ready, setReady] = useState(false);
  const [running, setRunning] = useState(false);
  const [trace, setTrace] = useState<string[]>([]);
  const [panels, setPanels] = useState<Record<"naive" | "stream", Panel>>(() => initialPanels("compare"));
  const sessionId = useRef(crypto.randomUUID());
  const turnId = useRef(crypto.randomUUID());
  const turnOpened = useRef(false);
  const revision = useRef(0);
  const completedCount = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const pendingQuery = useRef("");
  const lastSnapshotText = useRef("");
  const lastQueuedText = useRef("");
  const queuedSnapshot = useRef<SnapshotJob | null>(null);
  const snapshotDraining = useRef(false);
  const snapshotAbort = useRef(new AbortController());
  const runAbort = useRef(new AbortController());
  const snapshotEpoch = useRef(0);
  const turnEvents = useRef<EventSource | null>(null);
  const runEvents = useRef<EventSource | null>(null);

  useEffect(() => {
    snapshotAbort.current = new AbortController();
    runAbort.current = new AbortController();
    getHealth()
      .then((value) => {
        const isReady = value.index_ready;
        setReady(isReady);
        setHealth(
          isReady
            ? `${value.model} · answer ${value.reasoning_effort} / trigger ${value.trigger_reasoning_effort} · ${value.indexed_chunks} chunks`
            : `${value.dataset_status} · index missing, stale, or checksum-invalid`,
        );
      })
      .catch((error: Error) => setHealth(error.message));
    return () => {
      const abandonedTurnId = turnId.current;
      window.clearInterval(timer.current);
      snapshotAbort.current.abort();
      runAbort.current.abort();
      queuedSnapshot.current = null;
      turnEvents.current?.close();
      runEvents.current?.close();
      turnId.current = crypto.randomUUID();
      sessionId.current = crypto.randomUUID();
      if (turnOpened.current) {
        turnOpened.current = false;
        void cancelTurn(abandonedTurnId).catch(() => undefined);
      }
    };
  }, []);

  function log(event: BackendEvent) {
    setTrace((current) => [...current.slice(-39), `${event.type}${event.path ? ` · ${event.path}` : ""}`]);
    if (event.type === "trigger.decision") {
      setPanels((current) => ({
        ...current,
        stream: { ...current.stream, status: `${event.action}${event.query ? `: ${event.query}` : ""}` },
      }));
    } else if (event.type === "retrieval.started") {
      setPanels((current) => ({
        ...current,
        stream: { ...current.stream, status: "Retrieving before submit…" },
      }));
    } else if (event.type === "retrieval.ready") {
      setPanels((current) => ({
        ...current,
        stream: { ...current.stream, status: "Evidence ready before submit." },
      }));
    } else if (event.type === "retrieval.discarded") {
      setPanels((current) => ({
        ...current,
        stream: { ...current.stream, status: "Stale evidence discarded." },
      }));
    } else if (event.type === "retrieval.revalidated") {
      setPanels((current) => ({
        ...current,
        stream: { ...current.stream, status: "Earlier evidence revalidated for this text." },
      }));
    } else if (event.type === "retrieval.reused") {
      setPanels((current) => ({
        ...current,
        stream: {
          ...current.stream,
          status: event.ready_before_commit
            ? "Using evidence ready before Send."
            : event.retrieval_completed_before_commit
              ? "Using retrieval ready before Send and revalidated at Send."
            : "Using speculative retrieval that finished after Send.",
        },
      }));
    } else if (event.type === "retrieval.fallback") {
      setPanels((current) => ({
        ...current,
        stream: { ...current.stream, status: "Retrieving safely at Send…" },
      }));
    }
  }

  function handleRunEvent(event: BackendEvent) {
    log(event);
    if (!event.path) {
      if (event.type === "run.error") {
        const abandonedTurnId = turnId.current;
        setRunning(false);
        setHealth(event.message || "Run failed");
        turnEvents.current?.close();
        runEvents.current?.close();
        turnEvents.current = null;
        runEvents.current = null;
        snapshotAbort.current.abort();
        snapshotAbort.current = new AbortController();
        runAbort.current.abort();
        runAbort.current = new AbortController();
        queuedSnapshot.current = null;
        snapshotEpoch.current += 1;
        turnId.current = crypto.randomUUID();
        sessionId.current = crypto.randomUUID();
        turnOpened.current = false;
        revision.current = 0;
        lastSnapshotText.current = "";
        lastQueuedText.current = "";
        setPanels((current) => ({
          naive: mode === "stream" ? current.naive : { ...current.naive, status: event.message || "Run failed" },
          stream: mode === "naive" ? current.stream : { ...current.stream, status: event.message || "Run failed" },
        }));
        void cancelTurn(abandonedTurnId).catch(() => undefined);
      }
      return;
    }
    const path = event.path;
    setPanels((current) => {
      const panel = current[path];
      if (event.type === "answer.started") {
        return { ...current, [path]: { ...emptyPanel("Generating grounded answer…"), sources: event.sources || [] } };
      }
      if (event.type === "answer.delta") {
        return { ...current, [path]: { ...panel, answer: panel.answer + (event.text || "") } };
      }
      if (event.type === "answer.completed") {
        return {
          ...current,
          [path]: {
            ...panel,
            answer: event.answer || panel.answer,
            sources: event.sources || panel.sources,
            status: "Complete",
            firstToken: event.timing?.submit_to_first_token_ms ?? null,
            total: event.timing?.total_response_ms ?? null,
            cost: event.estimated_cost_usd?.total ?? null,
            accountingComplete:
              event.estimated_cost_usd?.accounting_complete ?? null,
            retrievalLead:
              event.timing?.accepted_retrieval_lead_at_commit_ms ?? null,
            candidateRetrievalLead:
              event.timing?.accepted_candidate_retrieval_lead_ms ?? null,
            reuseMode: event.reuse?.mode ?? null,
            cacheHit: event.retrieval?.cache_hit ?? null,
            controllerCalls: event.controller?.calls ?? null,
            retrievalCalls: event.retrieval?.calls ?? null,
            toolCalls: event.tool_traces?.length ?? null,
            fallbacks: event.reuse?.commit_fallbacks ?? null,
          },
        };
      }
      if (event.type === "answer.error") {
        return {
          ...current,
          [path]: { ...panel, status: event.message || "Path failed" },
        };
      }
      return current;
    });
    if (event.type === "answer.completed" || event.type === "answer.error") {
      const expected = mode === "compare" ? 2 : 1;
      completedCount.current += 1;
      if (completedCount.current >= expected) {
        setRunning(false);
        turnEvents.current?.close();
        runEvents.current?.close();
        turnEvents.current = null;
        runEvents.current = null;
        turnId.current = crypto.randomUUID();
        if (mode === "compare") sessionId.current = crypto.randomUUID();
        turnOpened.current = false;
        snapshotAbort.current.abort();
        snapshotAbort.current = new AbortController();
        runAbort.current.abort();
        runAbort.current = new AbortController();
        queuedSnapshot.current = null;
        snapshotEpoch.current += 1;
        revision.current = 0;
        lastSnapshotText.current = "";
        lastQueuedText.current = "";
      }
    }
  }

  async function snapshot(
    text: string,
    path: "stream" | "compare",
    targetTurnId: string,
    targetRevision: number,
    targetEpoch: number,
    signal: AbortSignal,
  ) {
    const normalized = text.trim();
    if (
      targetEpoch !== snapshotEpoch.current ||
      targetTurnId !== turnId.current ||
      !normalized ||
      normalized === lastSnapshotText.current
    ) return;
    turnOpened.current = true;
    await sendSnapshot({
      turnId: targetTurnId,
      sessionId: sessionId.current,
      path,
      revision: targetRevision,
      text: normalized,
      signal,
    });
    if (!turnEvents.current) {
      turnEvents.current = subscribe(
        `/v1/turns/${targetTurnId}/events`,
        log,
        () => setHealth("Typed-input event stream interrupted; reconnecting…"),
      );
    }
    if (
      targetEpoch === snapshotEpoch.current &&
      targetTurnId === turnId.current
    ) lastSnapshotText.current = normalized;
  }

  async function drainSnapshots() {
    if (snapshotDraining.current) return;
    snapshotDraining.current = true;
    try {
      while (queuedSnapshot.current) {
        const job = queuedSnapshot.current;
        queuedSnapshot.current = null;
        try {
          await snapshot(
            job.text,
            job.path,
            job.turnId,
            job.revision,
            job.epoch,
            job.signal,
          );
        } catch (error) {
          if (error instanceof Error && error.name !== "AbortError") {
            setHealth(error.message);
          }
        }
      }
    } finally {
      snapshotDraining.current = false;
      if (queuedSnapshot.current) void drainSnapshots();
    }
  }

  function queueSnapshot(text: string, path: "stream" | "compare") {
    revision.current += 1;
    queuedSnapshot.current = {
      text,
      path,
      turnId: turnId.current,
      revision: revision.current,
      epoch: snapshotEpoch.current,
      signal: snapshotAbort.current.signal,
    };
    void drainSnapshots();
  }

  function onQuery(value: string) {
    setQuery(value);
    pendingQuery.current = value;
    if (mode !== "naive" && timer.current === undefined) {
      // A real fixed clock keeps cumulative prefixes flowing during continuous
      // typing. Unchanged drafts are skipped locally rather than sent repeatedly.
      timer.current = window.setInterval(() => {
        const latest = pendingQuery.current.trim();
        if (!latest || latest === lastQueuedText.current) return;
        lastQueuedText.current = latest;
        queueSnapshot(latest, mode);
      }, 400);
    }
  }

  async function submit() {
    if (!query.trim() || running) return;
    setRunning(true);
    completedCount.current = 0;
    setTrace([]);
    setPanels({
      naive: emptyPanel(mode === "stream" ? "Not selected" : "Working…"),
      stream: emptyPanel(mode === "naive" ? "Not selected" : "Working…"),
    });
    window.clearInterval(timer.current);
    timer.current = undefined;
    // Send is the boundary: invalidate snapshots that have not already reached
    // the server, rather than waiting for them and creating extra typing headroom.
    snapshotAbort.current.abort();
    snapshotAbort.current = new AbortController();
    queuedSnapshot.current = null;
    snapshotEpoch.current += 1;
    revision.current += 1;
    runAbort.current.abort();
    runAbort.current = new AbortController();
    try {
      turnOpened.current = true;
      const accepted = await commit({
        turnId: turnId.current,
        sessionId: sessionId.current,
        path: mode,
        revision: revision.current,
        text: query.trim(),
        signal: runAbort.current.signal,
      });
      runEvents.current?.close();
      runEvents.current = subscribe(
        accepted.events_url,
        handleRunEvent,
        () => setHealth("Answer event stream interrupted; reconnecting…"),
      );
    } catch (error) {
      const abandonedTurnId = turnId.current;
      setRunning(false);
      setHealth(error instanceof Error ? error.message : "Request failed");
      turnEvents.current?.close();
      runEvents.current?.close();
      turnEvents.current = null;
      runEvents.current = null;
      snapshotAbort.current.abort();
      snapshotAbort.current = new AbortController();
      runAbort.current.abort();
      runAbort.current = new AbortController();
      queuedSnapshot.current = null;
      snapshotEpoch.current += 1;
      turnId.current = crypto.randomUUID();
      sessionId.current = crypto.randomUUID();
      turnOpened.current = false;
      revision.current = 0;
      lastSnapshotText.current = "";
      lastQueuedText.current = "";
      void cancelTurn(abandonedTurnId).catch(() => undefined);
    }
  }

  function newTurn() {
    const abandonedTurnId = turnId.current;
    window.clearInterval(timer.current);
    timer.current = undefined;
    turnEvents.current?.close();
    runEvents.current?.close();
    turnEvents.current = null;
    runEvents.current = null;
    snapshotEpoch.current += 1;
    snapshotAbort.current.abort();
    snapshotAbort.current = new AbortController();
    runAbort.current.abort();
    runAbort.current = new AbortController();
    queuedSnapshot.current = null;
    turnId.current = crypto.randomUUID();
    sessionId.current = crypto.randomUUID();
    turnOpened.current = false;
    revision.current = 0;
    pendingQuery.current = "";
    lastSnapshotText.current = "";
    lastQueuedText.current = "";
    setRunning(false);
    setQuery("");
    setTrace([]);
    setPanels(initialPanels(mode));
    void cancelTurn(abandonedTurnId).catch(() => undefined);
  }

  function changeMode(path: PathName) {
    if (path === mode || running) return;
    const abandonedTurnId = turnId.current;
    window.clearInterval(timer.current);
    timer.current = undefined;
    snapshotEpoch.current += 1;
    snapshotAbort.current.abort();
    snapshotAbort.current = new AbortController();
    runAbort.current.abort();
    runAbort.current = new AbortController();
    queuedSnapshot.current = null;
    turnEvents.current?.close();
    turnEvents.current = null;
    turnId.current = crypto.randomUUID();
    sessionId.current = crypto.randomUUID();
    turnOpened.current = false;
    revision.current = 0;
    lastSnapshotText.current = "";
    lastQueuedText.current = "";
    setMode(path);
    setTrace([]);
    setPanels(initialPanels(path));
    void cancelTurn(abandonedTurnId).catch(() => undefined);
  }

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">Applied AI Engineer assessment</p>
          <h1>Naive RAG vs typed StreamRAG</h1>
          <p className="subhead">
            Same answer model, corpus, retriever, endpoint controller, and prompt. Each path has
            isolated conversation state and a path-scoped retrieval cache.
          </p>
        </div>
        <span className={`health ${ready ? "ok" : "warn"}`}>{health}</span>
      </header>

      <section className="composer">
        <textarea
          aria-label="Question"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="Type a factual question…"
          disabled={running}
        />
        <div className="actions">
          <div className="modes" aria-label="Retrieval path">
            {(["naive", "stream", "compare"] as PathName[]).map((path) => (
              <button
                key={path}
                className={mode === path ? "active" : ""}
                aria-pressed={mode === path}
                onClick={() => changeMode(path)}
                disabled={running}
              >
                {path === "naive" ? "Path A" : path === "stream" ? "Path B" : "Compare"}
              </button>
            ))}
          </div>
          <div className="submit-row">
            <button className="secondary" onClick={newTurn}>{running ? "Cancel" : "New turn"}</button>
            <button className="primary" onClick={submit} disabled={!ready || !query.trim() || running}>
              {running ? "Running…" : "Send"}
            </button>
          </div>
        </div>
      </section>

      {mode === "compare" && <CompareSummary naive={panels.naive} stream={panels.stream} />}

      <section className="results">
        <Panel path="naive" title="Path A · Naive RAG" tone="amber" panel={panels.naive} />
        <Panel path="stream" title="Path B · StreamRAG" tone="green" panel={panels.stream} />
      </section>

      <details>
        <summary>Event trace</summary>
        <pre>{trace.length ? trace.join("\n") : "No events yet."}</pre>
      </details>
    </main>
  );
}

function CompareSummary({ naive, stream }: { naive: Panel; stream: Panel }) {
  const complete = naive.status === "Complete" && stream.status === "Complete";
  const naiveCalls = totalCalls(naive);
  const streamCalls = totalCalls(stream);
  return (
    <aside className="compare-summary" aria-label="Live comparison scope">
      <div>
        <strong>Live diagnostic, not an accuracy score.</strong>
        <span>
          The isolated paths run concurrently in this view. Inspect both answers and citations;
          reportable latency and correctness must come from an independent benchmark after the
          evaluation dataset is approved and frozen.
        </span>
      </div>
      {complete && (
        <div className="deltas" aria-label="StreamRAG minus Naive RAG">
          <Delta label="TTFT" value={difference(stream.firstToken, naive.firstToken, "ms")} />
          <Delta label="Total" value={difference(stream.total, naive.total, "ms")} />
          <Delta label="Cost" value={costDifference(stream, naive)} />
          <Delta label="Citations" value={signed(uniqueSourceCount(stream.sources) - uniqueSourceCount(naive.sources))} />
          <Delta label="Calls" value={difference(streamCalls, naiveCalls)} />
        </div>
      )}
    </aside>
  );
}

function Panel({ path, title, tone, panel }: { path: "naive" | "stream"; title: string; tone: string; panel: Panel }) {
  const visibleSources = Array.from(
    new Map(panel.sources.map((source) => [source.url || source.chunk_id, source])).values(),
  );
  return (
    <article className={`panel ${tone}`}>
      <div className="panel-head">
        <h2>{title}</h2>
        <span>{panel.status}</span>
      </div>
      <div className="metrics">
        <Metric label="Path TTFT" value={panel.firstToken === null ? "—" : `${panel.firstToken.toFixed(0)} ms`} />
        <Metric label="Total" value={panel.total === null ? "—" : `${panel.total.toFixed(0)} ms`} />
        <Metric label="Cost" value={panel.cost === null ? "—" : panel.accountingComplete === false ? `≥$${panel.cost.toFixed(4)} partial` : `$${panel.cost.toFixed(4)}`} />
        <Metric label="Accepted lead" value={panel.retrievalLead == null ? "—" : `${panel.retrievalLead.toFixed(0)} ms`} />
        <Metric label="Retrieval-ready lead" value={panel.candidateRetrievalLead == null ? "—" : `${panel.candidateRetrievalLead.toFixed(0)} ms`} />
        <Metric label="Evidence" value={evidenceLabel(path, panel)} />
        <Metric label="Path cache" value={panel.cacheHit == null ? "—" : panel.cacheHit ? "Hit" : "Miss"} />
        <Metric label="Citations" value={panel.status === "Complete" ? String(visibleSources.length) : "—"} />
        <Metric label="Calls · ctl / ret / tool" value={callBreakdown(panel)} />
        <Metric label="Send fallbacks" value={panel.fallbacks == null ? "—" : String(panel.fallbacks)} />
      </div>
      <div className={`answer ${panel.answer ? "" : "empty"}`}>{panel.answer || "No answer yet."}</div>
      <div className="sources">
        {visibleSources.map((source) => (
          <a key={source.chunk_id} href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
        ))}
      </div>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><strong>{value}</strong></div>;
}

function Delta({ label, value }: { label: string; value: string }) {
  return <span><small>{label}</small><strong>{value}</strong></span>;
}

function uniqueSourceCount(sources: Source[]) {
  return new Set(sources.map((source) => source.url || source.chunk_id)).size;
}

function totalCalls(panel: Panel) {
  const calls = [panel.controllerCalls, panel.retrievalCalls, panel.toolCalls];
  return calls.every((value) => value === null)
    ? null
    : calls.reduce<number>((total, value) => total + (value ?? 0), 0);
}

function callBreakdown(panel: Panel) {
  const calls = [panel.controllerCalls, panel.retrievalCalls, panel.toolCalls];
  return calls.every((value) => value === null)
    ? "—"
    : calls.map((value) => value ?? 0).join(" / ");
}

function evidenceLabel(path: "naive" | "stream", panel: Panel) {
  if (panel.reuseMode === "precommit_exact") return "Ready before Send";
  if (panel.reuseMode === "precommit_revalidated") return "Revalidated";
  if (panel.reuseMode === "presubmit_retrieval_revalidated_at_commit") return "Prefetched · checked at Send";
  if (panel.reuseMode === "inflight_completed_postcommit") return "In-flight overlap";
  if (panel.reuseMode === "commit_endpoint") {
    return path === "stream" && panel.fallbacks ? "Send fallback" : "At Send";
  }
  return "—";
}

function difference(left: number | null, right: number | null, unit = "") {
  if (left === null || right === null) return "—";
  const value = left - right;
  return `${signed(Math.round(value))}${unit ? ` ${unit}` : ""}`;
}

function costDifference(left: Panel, right: Panel) {
  if (left.cost === null || right.cost === null) return "—";
  if (left.accountingComplete !== true || right.accountingComplete !== true) return "partial";
  const value = left.cost - right.cost;
  return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(4)}`;
}

function signed(value: number) {
  if (value > 0) return `+${value}`;
  if (value < 0) return `−${Math.abs(value)}`;
  return "0";
}
