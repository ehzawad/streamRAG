export type AnswerPath = "naive" | "stream";
export type RunMode = AnswerPath | "compare";

export type RunLifecycleEvent = {
  type: string;
  path?: AnswerPath;
};

export const SSE_EVENT_TYPES = [
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
  "answer.ready",
  "answer.completed",
  "answer.error",
  "agent.tool_started",
  "agent.tool_completed",
  "agent.context_compressed",
  "run.started",
  "run.completed",
  "run.error",
] as const;

export function recordUserVisibleTerminal(
  completedPaths: Set<AnswerPath>,
  event: RunLifecycleEvent,
): boolean {
  if (
    !event.path ||
    (event.type !== "answer.ready" && event.type !== "answer.error") ||
    completedPaths.has(event.path)
  ) return false;
  completedPaths.add(event.path);
  return true;
}

export function isUserVisibleRunComplete(
  mode: RunMode,
  completedPaths: ReadonlySet<AnswerPath>,
): boolean {
  if (mode === "compare") {
    return completedPaths.has("naive") && completedPaths.has("stream");
  }
  return completedPaths.has(mode);
}
