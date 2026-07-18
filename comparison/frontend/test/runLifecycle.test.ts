import assert from "node:assert/strict";
import test from "node:test";

import {
  COMMIT_REQUEST_TIMEOUT_MS,
  isRunTransportTerminal,
  isUserVisibleRunComplete,
  POST_ANSWER_LEASE_BUDGET_MS,
  recordUserVisibleTerminal,
  selectedImplementations,
  SNAPSHOT_REQUEST_TIMEOUT_MS,
  SSE_EVENT_TYPES,
  startSelectedRuns,
} from "../src/runLifecycle.ts";

test("answer.ready ends visible waiting without ending the telemetry transport", () => {
  const completed = new Set<"naive" | "stream">();

  assert.equal(
    recordUserVisibleTerminal(completed, { type: "answer.completed", path: "naive" }),
    false,
  );
  assert.equal(isUserVisibleRunComplete("naive", completed), false);
  assert.equal(
    recordUserVisibleTerminal(completed, { type: "answer.ready", path: "naive" }),
    true,
  );
  assert.equal(isUserVisibleRunComplete("naive", completed), true);
  assert.equal(
    recordUserVisibleTerminal(completed, { type: "answer.ready", path: "naive" }),
    false,
  );
  assert.equal(completed.size, 1);
  assert.equal(isRunTransportTerminal({ type: "answer.ready", path: "naive" }), false);
  assert.equal(isRunTransportTerminal({ type: "answer.completed", path: "naive" }), false);
  assert.equal(isRunTransportTerminal({ type: "run.completed" }), true);
  assert.equal(isRunTransportTerminal({ type: "run.error" }), true);
});

test("compare mode waits only for each path's ready-or-error boundary", () => {
  const completed = new Set<"naive" | "stream">();

  recordUserVisibleTerminal(completed, { type: "answer.ready", path: "naive" });
  assert.equal(isUserVisibleRunComplete("compare", completed), false);
  recordUserVisibleTerminal(completed, { type: "answer.error", path: "stream" });
  assert.equal(isUserVisibleRunComplete("compare", completed), true);
  assert.equal(SSE_EVENT_TYPES.includes("answer.ready"), true);
});

test("a service-level run error is a visible terminal for its isolated path", () => {
  const completed = new Set<"naive" | "stream">();
  assert.equal(
    recordUserVisibleTerminal(completed, { type: "run.error", path: "stream" }),
    true,
  );
  assert.equal(isUserVisibleRunComplete("stream", completed), true);
});

test("compare starts both isolated commits before awaiting either one", async () => {
  const started: string[] = [];
  const releases = new Map<string, () => void>();
  const pending = startSelectedRuns("compare", (path) => {
    started.push(path);
    return new Promise<string>((resolve) => releases.set(path, () => resolve(path)));
  });

  assert.deepEqual(started, ["naive", "stream"]);
  releases.get("stream")?.();
  releases.get("naive")?.();
  assert.deepEqual(await pending, [
    { path: "naive", status: "fulfilled", value: "naive" },
    { path: "stream", status: "fulfilled", value: "stream" },
  ]);
  assert.deepEqual(selectedImplementations("naive"), ["naive"]);
  assert.deepEqual(selectedImplementations("stream"), ["stream"]);
});

test("follow-up request deadlines exceed the server persistence lease budget", () => {
  assert.ok(SNAPSHOT_REQUEST_TIMEOUT_MS > POST_ANSWER_LEASE_BUDGET_MS);
  assert.ok(COMMIT_REQUEST_TIMEOUT_MS > POST_ANSWER_LEASE_BUDGET_MS);
});
