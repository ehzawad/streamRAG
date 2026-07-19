/* @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App.tsx";

type RequestRecord = {
  method: string;
  url: string;
};

class TestEventSource extends EventTarget {
  static instances: TestEventSource[] = [];

  readonly url: string;
  readonly withCredentials = false;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readyState = this.OPEN;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string | URL) {
    super();
    this.url = String(url);
    TestEventSource.instances.push(this);
  }

  close = vi.fn(() => {
    this.readyState = this.CLOSED;
  });

  emit(type: string, body: Record<string, unknown>) {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(body) }));
  }
}

function response(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: status === 204 ? undefined : { "Content-Type": "application/json" },
  });
}

function health() {
  return {
    ok: true,
    implementation: "naive",
    metrics_contract_version: 1,
    supports_snapshots: false,
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
    service_tier: "default",
    instance_id: "naive-test-instance",
  };
}

function dataStatus() {
  return {
    implementation: "naive",
    metrics_contract_version: 1,
    supports_snapshots: false,
    index_matches_current_corpus: true,
  };
}

describe("App run lifecycle", () => {
  let requests: RequestRecord[];

  beforeEach(() => {
    requests = [];
    TestEventSource.instances = [];
    vi.stubGlobal("EventSource", TestEventSource);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      requests.push({ method, url });
      if (url.endsWith("/v1/health")) return response(health());
      if (url.endsWith("/v1/data/status")) return response(dataStatus());
      if (url.endsWith("/commit")) {
        return response({
          run_id: "naive-run",
          turn_id: "naive-turn",
          path: "naive",
          events_url: "/v1/runs/naive-run/events",
        }, 202);
      }
      if (method === "DELETE") return response(null, 204);
      throw new Error(`Unexpected request: ${method} ${url}`);
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function startRun(question: string): Promise<TestEventSource> {
    render(<App mode="naive" />);
    await waitFor(() => {
      expect(screen.getByText(/naive API/)).toBeTruthy();
    });

    fireEvent.change(screen.getByRole("textbox", { name: "Question" }), {
      target: { value: question },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(TestEventSource.instances).toHaveLength(1);
    });
    return TestEventSource.instances[0];
  }

  it("renders a committed question and its streamed grounded answer", async () => {
    const source = await startRun("Who won the Oscar for Black Swan?");
    const citation = {
      chunk_id: "black-swan-1",
      title: "Black Swan awards",
      url: "https://example.test/black-swan",
      score: 0.98,
    };

    act(() => {
      source.emit("answer.started", { type: "answer.started", sources: [citation] });
      source.emit("answer.delta", { type: "answer.delta", text: "Natalie " });
      source.emit("answer.ready", {
        type: "answer.ready",
        answer: "Natalie Portman",
        sources: [citation],
        timing: {
          submit_to_first_token_ms: 120,
          total_response_ms: 420,
          accepted_retrieval_lead_at_commit_ms: 0,
          accepted_candidate_retrieval_lead_ms: 0,
        },
        persistence: { status: "pending", elapsed_ms: null },
      });
      source.emit("run.completed", { type: "run.completed" });
    });

    const conversation = await screen.findByRole("region", { name: "Conversation" });
    expect(within(conversation).getByText("Who won the Oscar for Black Swan?")).toBeTruthy();
    expect(within(conversation).getByText("Natalie Portman")).toBeTruthy();
    expect(
      within(conversation).getByRole("link", { name: "Black Swan awards" }).getAttribute("href"),
    ).toBe("https://example.test/black-swan");
    expect(source.close).toHaveBeenCalledOnce();
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Question" }).disabled)
      .toBe(false);
  });

  it("cancels an active backend turn and clears the transcript on New chat", async () => {
    const source = await startRun("Which film opened higher?");
    expect(screen.getByText("Which film opened higher?")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    await waitFor(() => {
      expect(requests.some(({ method, url }) => method === "DELETE" && url.includes("/v1/turns/")))
        .toBe(true);
    });
    expect(source.close).toHaveBeenCalledOnce();
    expect(screen.queryByRole("region", { name: "Conversation" })).toBeNull();
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Question" }).value).toBe("");
  });
});
