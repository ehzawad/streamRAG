# ruff: noqa: E501

from __future__ import annotations

import json

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" href="data:,">
  <title>__TITLE__</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; color: #edf2ee; background: #0b100e; }
    main { width: min(900px, calc(100% - 32px)); margin: 0 auto; padding: 64px 0; }
    header { display: flex; justify-content: space-between; gap: 24px; align-items: start; }
    .eyebrow { color: #91d8ad; font: 700 12px/1.2 ui-monospace, monospace; letter-spacing: .12em; }
    h1 { margin: 10px 0 8px; font-size: clamp(36px, 8vw, 68px); letter-spacing: -.055em; }
    .subtitle { max-width: 650px; color: #aebbb4; font-size: 17px; line-height: 1.6; }
    .health { border: 1px solid #34433b; border-radius: 999px; padding: 8px 12px; white-space: nowrap; }
    .health.ok { color: #91d8ad; border-color: #39654a; }
    section { margin-top: 32px; padding: 22px; border: 1px solid #26312b; border-radius: 18px; background: #111815; }
    textarea { width: 100%; min-height: 150px; resize: vertical; border: 0; outline: 0; color: inherit; background: transparent; font: 500 20px/1.55 inherit; }
    .actions { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-top: 18px; }
    .status { color: #91a097; font-size: 14px; }
    button { border: 1px solid #36443d; border-radius: 10px; padding: 11px 16px; color: #e9f2ec; background: #19231e; cursor: pointer; font-weight: 700; }
    button.primary { color: #07110b; background: #8fe0ad; border-color: #8fe0ad; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    .buttons { display: flex; gap: 8px; }
    .answer { min-height: 110px; white-space: pre-wrap; font-size: 18px; line-height: 1.65; }
    .empty { color: #708078; }
    .conversation { display: grid; gap: 18px; }
    .turn { display: grid; gap: 10px; }
    .message { padding: 13px 15px; border-radius: 14px; line-height: 1.6; white-space: pre-wrap; }
    .message.user { justify-self: end; width: min(720px, 88%); color: #07110b; background: #8fe0ad; border-bottom-right-radius: 4px; }
    .message.assistant { border: 1px solid #34433b; background: #0c120f; border-top-left-radius: 4px; }
    .message-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 6px; color: #91a097; font-size: 11px; }
    .message-sources { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .message-sources a { color: #9ce3b6; font-size: 12px; text-decoration: none; border-bottom: 1px solid #467357; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit,minmax(130px,1fr)); gap: 10px; margin-bottom: 22px; }
    .metric { padding: 12px; border-radius: 10px; background: #0c120f; }
    .metric small { display: block; color: #7f9187; margin-bottom: 5px; }
    .sources { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
    .sources a { color: #9ce3b6; text-decoration: none; border-bottom: 1px solid #467357; }
    footer { margin-top: 24px; color: #68766e; font-size: 13px; }
    footer a { color: #8fa299; }
    @media (max-width: 680px) { header { display: block; } .health { display: inline-block; margin-top: 14px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <div class="eyebrow">INDEPENDENT RAG SERVICE · __ROLE__</div>
      <h1>__TITLE__</h1>
      <p class="subtitle">__SUBTITLE__</p>
    </div>
    <span id="health" class="health">Connecting…</span>
  </header>
  <section>
    <div id="conversation" class="conversation">
      <div id="conversation-empty" class="empty">No messages yet.</div>
    </div>
  </section>
  <section>
    <textarea id="question" aria-label="Question" placeholder="Type a factual question…"></textarea>
    <div class="actions">
      <span id="status" class="status">Ready for a question.</span>
      <div class="buttons">
        <button id="reset" type="button">New chat</button>
        <button id="send" class="primary" type="button" disabled>Send</button>
      </div>
    </div>
  </section>
  <section aria-live="polite">
    <div class="metrics">
      <div class="metric"><small>First token</small><strong id="ttft">—</strong></div>
      <div class="metric"><small>Total</small><strong id="total">—</strong></div>
      <div class="metric"><small>Cost</small><strong id="cost">—</strong></div>
      <div class="metric"><small>Evidence reuse</small><strong id="reuse">—</strong></div>
    </div>
    <div id="answer" class="answer empty">No answer yet.</div>
    <div id="sources" class="sources"></div>
  </section>
  <footer>This UI talks only to its own service. <a href="/docs">OpenAPI docs</a></footer>
</main>
<script>
const config = __BOOTSTRAP__;
const byId = (id) => document.getElementById(id);
const question = byId("question");
const sendButton = byId("send");
let sessionId;
let turnId;
let revision;
let snapshotTimer;
let turnEvents;
let runEvents;
let sent;
let lastSnapshot;
let snapshotQueue;
let activeTranscript;
let editingAfterRun;
let lifecycleEpoch = 0;
let commitController;

function id() { return crypto.randomUUID(); }
function setStatus(text) { byId("status").textContent = text; }
function clearDiagnostics() {
  byId("answer").textContent = "No answer yet.";
  byId("answer").classList.add("empty");
  byId("sources").replaceChildren();
  ["ttft", "total", "cost", "reuse"].forEach((name) => {
    byId(name).textContent = "—";
  });
}

function resetState(cancel = true, newConversation = false) {
  lifecycleEpoch += 1;
  commitController?.abort();
  commitController = null;
  if (cancel && turnId) {
    fetch(`/v1/turns/${encodeURIComponent(turnId)}`, {
      method: "DELETE",
      keepalive: true,
    }).catch(() => undefined);
  }
  clearTimeout(snapshotTimer);
  snapshotQueue?.controller?.abort();
  snapshotQueue = { active: false, controller: null, latest: "" };
  turnEvents?.close();
  turnEvents = null;
  runEvents?.close();
  runEvents = null;
  if (newConversation || !sessionId) sessionId = id();
  turnId = id();
  revision = 0;
  sent = false;
  lastSnapshot = "";
  question.disabled = false;
  question.value = "";
  sendButton.disabled = true;
  activeTranscript = null;
  editingAfterRun = false;
  clearDiagnostics();
  if (newConversation) {
    const empty = document.createElement("div");
    empty.id = "conversation-empty";
    empty.className = "empty";
    empty.textContent = "No messages yet.";
    byId("conversation").replaceChildren(empty);
  }
  setStatus(config.supportsSnapshots ? "Type to prepare evidence." : "Ready for a question.");
}

function prepareNextTurn(nextStatus = "Ask a follow-up; this chat keeps context.") {
  lifecycleEpoch += 1;
  commitController?.abort();
  commitController = null;
  clearTimeout(snapshotTimer);
  snapshotQueue?.controller?.abort();
  snapshotQueue = { active: false, controller: null, latest: "" };
  turnEvents?.close();
  turnEvents = null;
  runEvents?.close();
  runEvents = null;
  turnId = id();
  revision = 0;
  sent = false;
  lastSnapshot = "";
  activeTranscript = null;
  editingAfterRun = true;
  question.disabled = false;
  question.value = "";
  sendButton.disabled = true;
  setStatus(nextStatus);
}

function appendTranscriptTurn(text) {
  byId("conversation-empty")?.remove();
  const turn = document.createElement("article");
  turn.className = "turn";

  const user = document.createElement("div");
  user.className = "message user";
  user.textContent = text;

  const assistant = document.createElement("div");
  assistant.className = "message assistant";
  const head = document.createElement("div");
  head.className = "message-head";
  const role = document.createElement("strong");
  role.textContent = config.implementation === "stream" ? "StreamRAG" : "Naive RAG";
  const state = document.createElement("span");
  state.textContent = "Waiting…";
  head.append(role, state);
  const answer = document.createElement("div");
  answer.className = "empty";
  answer.textContent = "Waiting for the grounded answer…";
  const sources = document.createElement("div");
  sources.className = "message-sources";
  assistant.append(head, answer, sources);
  turn.append(user, assistant);
  byId("conversation").append(turn);
  return { answer, sources, state };
}

async function post(path, body, signal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error((await response.text()) || `Request failed: ${response.status}`);
  return response.json();
}

function subscribe(path, handler) {
  const source = new EventSource(path);
  const names = [
    "draft.settled", "trigger.decision", "retrieval.started", "retrieval.ready",
    "retrieval.discarded", "retrieval.revalidated", "retrieval.reused",
    "retrieval.fallback", "answer.started", "answer.delta", "answer.ready",
    "answer.completed", "answer.error", "run.completed", "run.error",
  ];
  const dispatch = (event) => handler(JSON.parse(event.data));
  source.onmessage = dispatch;
  names.forEach((name) => source.addEventListener(name, dispatch));
  source.onerror = () => setStatus("Event stream reconnecting…");
  return source;
}

async function drainSnapshotQueue(queue) {
  if (queue !== snapshotQueue || queue.active || sent) return;
  const text = queue.latest;
  queue.latest = "";
  if (!text || text === lastSnapshot) return;
  queue.active = true;
  const controller = new AbortController();
  queue.controller = controller;
  try {
    revision += 1;
    const accepted = await post(`/v1/turns/${turnId}/snapshots`, {
      session_id: sessionId,
      revision,
      text,
    }, controller.signal);
    if (queue !== snapshotQueue || sent) return;
    lastSnapshot = text;
    if (!turnEvents) {
      const epoch = lifecycleEpoch;
      turnEvents = subscribe(accepted.events_url, (event) => {
        if (epoch === lifecycleEpoch) handleTypedEvent(event);
      });
    }
  } catch (error) {
    if (queue === snapshotQueue && !sent && error.name !== "AbortError") {
      setStatus(error.message);
    }
  } finally {
    if (queue.controller === controller) queue.controller = null;
    queue.active = false;
    if (queue === snapshotQueue && !sent && queue.latest && queue.latest !== lastSnapshot) {
      void drainSnapshotQueue(queue);
    }
  }
}

function queueSnapshot() {
  if (!config.supportsSnapshots || sent) return;
  const text = question.value.trim();
  if (!text || text === lastSnapshot) {
    snapshotQueue.latest = "";
    return;
  }
  snapshotQueue.latest = text;
  void drainSnapshotQueue(snapshotQueue);
}

function handleTypedEvent(event) {
  if (sent) return;
  if (event.type === "retrieval.ready") setStatus("Evidence prepared. Press Send for the answer.");
  else if (event.type === "retrieval.started") setStatus("Preparing evidence while you type…");
  else if (event.type === "retrieval.discarded") setStatus("Draft changed; stale evidence discarded.");
  else if (event.type === "trigger.decision") {
    setStatus(`${event.action || "Waiting"}${event.query ? ` · ${event.query}` : ""}`);
  }
}

function renderSourcesInto(container, sources = []) {
  container.replaceChildren();
  const unique = new Map(sources.map((source) => [source.url || source.chunk_id, source]));
  unique.forEach((source) => {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = source.title || source.chunk_id;
    container.append(link);
  });
}

function renderSources(sources = []) {
  renderSourcesInto(byId("sources"), sources);
  if (activeTranscript) renderSourcesInto(activeTranscript.sources, sources);
}

function handleRunEvent(event) {
  if (!sent) return;
  if (event.type === "answer.started") {
    setStatus("Generating grounded answer…");
    if (activeTranscript) activeTranscript.state.textContent = "Generating…";
  }
  if (event.type === "answer.delta") {
    byId("answer").classList.remove("empty");
    byId("answer").textContent += event.text || "";
    if (activeTranscript) {
      activeTranscript.answer.classList.remove("empty");
      activeTranscript.answer.textContent = activeTranscript.answer.textContent === "Waiting for the grounded answer…"
        ? event.text || ""
        : activeTranscript.answer.textContent + (event.text || "");
    }
  }
  if (event.type === "answer.ready" || event.type === "answer.completed") {
    byId("answer").classList.remove("empty");
    byId("answer").textContent = event.answer || byId("answer").textContent;
    if (activeTranscript) {
      activeTranscript.answer.classList.remove("empty");
      activeTranscript.answer.textContent = event.answer || activeTranscript.answer.textContent;
      activeTranscript.state.textContent = event.type === "answer.ready" ? "Answer ready" : "Complete";
    }
    renderSources(event.sources);
    setStatus(event.type === "answer.ready" ? "Answer ready; finalizing metrics…" : "Complete");
    const timing = event.timing || {};
    const estimate = event.estimated_cost_usd || {};
    byId("ttft").textContent = timing.submit_to_first_token_ms == null
      ? "—" : `${Math.round(timing.submit_to_first_token_ms)} ms`;
    byId("total").textContent = timing.total_response_ms == null
      ? "—" : `${Math.round(timing.total_response_ms)} ms`;
    byId("cost").textContent = estimate.total == null
      ? "—" : `$${Number(estimate.total).toFixed(4)}`;
    byId("reuse").textContent = event.reuse?.mode || "—";
  }
  if (event.type === "answer.error" || event.type === "run.error") {
    setStatus(event.message || "Run failed");
    if (activeTranscript) activeTranscript.state.textContent = event.message || "Run failed";
    question.disabled = false;
  }
  if (event.type === "run.completed") prepareNextTurn();
  if (event.type === "run.error") {
    prepareNextTurn(`${event.message || "Run failed"} You can retry or ask another question.`);
  }
}

question.addEventListener("input", () => {
  if (editingAfterRun) {
    editingAfterRun = false;
    clearDiagnostics();
  }
  sendButton.disabled = !question.value.trim() || sent;
  if (!config.supportsSnapshots || sent) return;
  snapshotQueue.latest = "";
  clearTimeout(snapshotTimer);
  snapshotTimer = setTimeout(queueSnapshot, 400);
});

sendButton.addEventListener("click", async () => {
  const text = question.value.trim();
  if (!text || sent) return;
  sent = true;
  clearTimeout(snapshotTimer);
  snapshotQueue.latest = "";
  snapshotQueue.controller?.abort();
  turnEvents?.close();
  turnEvents = null;
  question.disabled = true;
  sendButton.disabled = true;
  byId("answer").textContent = "";
  byId("answer").classList.remove("empty");
  activeTranscript = appendTranscriptTurn(text);
  setStatus("Submitting committed text…");
  const epoch = lifecycleEpoch;
  const controller = new AbortController();
  commitController = controller;
  try {
    revision += 1;
    const accepted = await post(`/v1/turns/${turnId}/commit`, {
      session_id: sessionId,
      revision,
      text,
      query_time: new Date().toISOString(),
    }, controller.signal);
    if (epoch !== lifecycleEpoch || controller.signal.aborted) return;
    if (accepted.path !== config.implementation) throw new Error("Service role mismatch");
    runEvents = subscribe(accepted.events_url, (event) => {
      if (epoch === lifecycleEpoch) handleRunEvent(event);
    });
  } catch (error) {
    if (epoch !== lifecycleEpoch || error.name === "AbortError") return;
    sent = false;
    question.disabled = false;
    sendButton.disabled = false;
    if (activeTranscript) activeTranscript.state.textContent = error.message;
    setStatus(error.message);
  } finally {
    if (commitController === controller) commitController = null;
  }
});

byId("reset").addEventListener("click", () => resetState(true, true));
fetch("/v1/health").then((response) => response.json()).then((health) => {
  const node = byId("health");
  node.textContent = health.index_ready
    ? `${health.implementation} · ${health.indexed_chunks} chunks`
    : `${health.implementation} · index unavailable`;
  node.classList.toggle("ok", Boolean(health.index_ready));
}).catch(() => byId("health").textContent = "Service unavailable");
resetState(false);
</script>
</body>
</html>
"""


def standalone_page(
    *,
    implementation: str,
    supports_snapshots: bool,
    title: str,
    subtitle: str,
) -> str:
    bootstrap = json.dumps(
        {
            "implementation": implementation,
            "supportsSnapshots": supports_snapshots,
        },
        separators=(",", ":"),
    )
    return (
        _PAGE.replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__ROLE__", implementation.upper())
        .replace("__BOOTSTRAP__", bootstrap)
    )
