import assert from "node:assert/strict";
import test from "node:test";

import {
  isUserVisibleRunComplete,
  recordUserVisibleTerminal,
  SSE_EVENT_TYPES,
} from "../src/runLifecycle.ts";

test("answer.ready is the user-visible boundary and answer.completed is accounting-only", () => {
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
});

test("compare mode waits only for each path's ready-or-error boundary", () => {
  const completed = new Set<"naive" | "stream">();

  recordUserVisibleTerminal(completed, { type: "answer.ready", path: "naive" });
  assert.equal(isUserVisibleRunComplete("compare", completed), false);
  recordUserVisibleTerminal(completed, { type: "answer.error", path: "stream" });
  assert.equal(isUserVisibleRunComplete("compare", completed), true);
  assert.equal(SSE_EVENT_TYPES.includes("answer.ready"), true);
});
