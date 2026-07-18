import assert from "node:assert/strict";
import test from "node:test";

import {
  EXPERIENCE_PATHS,
  modeForPath,
  normalizedPath,
  pathForMode,
} from "../src/routes.ts";

test("the homepage exposes one stable route for each experience", () => {
  assert.deepEqual(EXPERIENCE_PATHS, {
    naive: "/naive",
    stream: "/stream",
    compare: "/compare",
  });
  assert.equal(pathForMode("naive"), "/naive");
  assert.equal(pathForMode("stream"), "/stream");
  assert.equal(pathForMode("compare"), "/compare");
});

test("deep links tolerate a trailing slash and reject unknown paths", () => {
  assert.equal(normalizedPath("/"), "/");
  assert.equal(modeForPath("/naive/"), "naive");
  assert.equal(modeForPath("/stream"), "stream");
  assert.equal(modeForPath("/compare/"), "compare");
  assert.equal(modeForPath("/unknown"), null);
});
