import assert from "node:assert/strict";
import test, { afterEach } from "node:test";

import {
  commit,
  getServiceTopology,
  sendSnapshot,
  type ServiceDataStatus,
  validateCommonIdentity,
  validateServiceIsolation,
} from "../src/api.ts";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("snapshot and commit requests target isolated services without a path field", async () => {
  const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    requests.push({ url, body });
    if (url.includes("/snapshots")) {
      return response({ turn_id: "stream-turn", revision: 2, events_url: "/events" }, 202);
    }
    const implementation = url.includes(":8001") ? "naive" : "stream";
    return response({
      run_id: `${implementation}-run`,
      turn_id: `${implementation}-turn`,
      path: implementation,
      events_url: `/v1/runs/${implementation}-run/events`,
    }, 202);
  };

  await sendSnapshot({
    turnId: "stream-turn",
    sessionId: "stream-session",
    revision: 2,
    text: "draft",
  });
  await Promise.all([
    commit({
      implementation: "naive",
      turnId: "naive-turn",
      sessionId: "naive-session",
      revision: 1,
      text: "question",
      queryTime: "2026-07-19T00:00:00.000Z",
    }),
    commit({
      implementation: "stream",
      turnId: "stream-turn",
      sessionId: "stream-session",
      revision: 3,
      text: "question",
      queryTime: "2026-07-19T00:00:00.000Z",
    }),
  ]);

  assert.match(requests[0].url, /^http:\/\/localhost:8002\/v1\/turns\/stream-turn\/snapshots$/);
  assert.match(requests[1].url, /^http:\/\/localhost:8001\/v1\/turns\/naive-turn\/commit$/);
  assert.match(requests[2].url, /^http:\/\/localhost:8002\/v1\/turns\/stream-turn\/commit$/);
  requests.forEach(({ body }) => assert.equal("path" in body, false));
  assert.equal(requests[1].body.query_time, requests[2].body.query_time);
});

function dataStatus(implementation: "naive" | "stream"): ServiceDataStatus {
  return {
    implementation,
    metrics_contract_version: 1,
    supports_snapshots: implementation === "stream",
    approval_status: "candidate_pending_human_review",
    indexed_chunks: 1000,
    indexed_desired_chunks: 1000,
    index_checksum: "index",
    index_source_sha256: "index-source",
    current_index_source_sha256: "index-source",
    index_matches_current_corpus: true,
    model: "gpt-test",
    embedding_model: "embedding-test",
    reasoning_effort: "medium",
    summary_reasoning_effort: "low",
    ...(implementation === "stream"
      ? { trigger_reasoning_effort: "low", settled_draft_delay_ms: 800 }
      : {}),
    service_tier: "default",
    configuration: { retrieval_top_k: 5 },
    shared_source_sha256: "shared-source",
    config_hash: "config",
    dataset_checksum: "dataset",
    serving_dataset_checksum: "serving",
    freeze_id: "freeze",
    dataset_sha256: "dataset-sha",
    documents_sha256: "documents-sha",
  };
}

function health(implementation: "naive" | "stream") {
  return {
    ok: true,
    implementation,
    metrics_contract_version: 1,
    supports_snapshots: implementation === "stream",
    dataset_status: "candidate_pending_human_review",
    indexed_chunks: 1000,
    indexed_desired_chunks: 1000,
    index_ready: true,
    dataset_checksums_valid: true,
    index_matches_current_corpus: true,
    model: "gpt-test",
    embedding_model: "embedding-test",
    reasoning_effort: "medium",
    summary_reasoning_effort: "low",
    ...(implementation === "stream"
      ? { trigger_reasoning_effort: "low", settled_draft_delay_ms: 800 }
      : {}),
    service_tier: "default",
    instance_id: `${implementation}-instance`,
  };
}

test("topology validation checks service roles, metrics contract, and shared identity", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    const implementation = url.includes(":8001") ? "naive" : "stream";
    return response(url.endsWith("/v1/health") ? health(implementation) : dataStatus(implementation));
  };

  const topology = await getServiceTopology();
  assert.deepEqual(topology.errors, {});
  assert.equal(topology.comparisonError, null);
  assert.equal(topology.services.naive?.health.implementation, "naive");
  assert.equal(topology.services.stream?.health.supports_snapshots, true);
});

test("topology rejects a URL serving the wrong implementation role", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    const expected = url.includes(":8001") ? "naive" : "stream";
    if (url.endsWith("/v1/health") && expected === "naive") {
      return response(health("stream"));
    }
    return response(url.endsWith("/v1/health") ? health(expected) : dataStatus(expected));
  };

  const topology = await getServiceTopology();
  assert.match(topology.errors.naive ?? "", /wrong implementation role/);
  assert.equal(topology.services.naive, undefined);
  assert.equal(topology.services.stream?.health.implementation, "stream");
});

test("topology rejects an incompatible metrics contract", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    const implementation = url.includes(":8001") ? "naive" : "stream";
    if (url.endsWith("/v1/data/status") && implementation === "stream") {
      return response({ ...dataStatus("stream"), metrics_contract_version: 2 });
    }
    return response(
      url.endsWith("/v1/health") ? health(implementation) : dataStatus(implementation),
    );
  };

  const topology = await getServiceTopology();
  assert.match(topology.errors.stream ?? "", /incompatible metrics contract/);
  assert.equal(topology.services.stream, undefined);
});

test("comparison refuses services with different common identities", () => {
  const naive = dataStatus("naive");
  const stream = { ...dataStatus("stream"), documents_sha256: "different" };
  assert.throws(
    () => validateCommonIdentity(naive, stream),
    /service identity mismatch: documents_sha256/,
  );
});

test("comparison refuses the same process identity for both roles", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    const implementation = url.includes(":8001") ? "naive" : "stream";
    const body = url.endsWith("/v1/health")
      ? { ...health(implementation), instance_id: "shared-instance" }
      : dataStatus(implementation);
    return response(body);
  };

  const topology = await getServiceTopology();
  assert.match(topology.comparisonError ?? "", /distinct service instances/);
});

test("comparison refuses one resolved URL for both roles", () => {
  const naive = { health: health("naive"), data: dataStatus("naive") };
  const stream = { health: health("stream"), data: dataStatus("stream") };
  assert.throws(
    () => validateServiceIsolation(naive, stream, "http://localhost:8001/", "http://localhost:8001"),
    /distinct service URLs/,
  );
});
