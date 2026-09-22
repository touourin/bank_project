import type { Run } from "../alignment/types";
import type { Dataset } from "../knowledge/types";

export type TaskPhase =
  "waiting" | "running" | "review" | "completed" | "failed";

/** Interpret source statuses once; adapters retain the source-specific progress text. */
export function taskPhase(status?: string): TaskPhase {
  if (
    ["queued", "running", "indexing", "analyzing", "building"].includes(
      status ?? "",
    )
  )
    return "running";
  if (["failed", "interrupted"].includes(status ?? "")) return "failed";
  if (["succeeded", "completed"].includes(status ?? "")) return "completed";
  return status === "ready" ? "review" : "waiting";
}

export const isRunningTask = (task?: { status: string } | null) =>
  taskPhase(task?.status) === "running";
export const taskRevision = (task: { revision: number } | null) =>
  task?.revision ?? -1;
export const isTableTaskRunning = (run?: Run | null) =>
  isRunningTask(run) || taskPhase(run?.graph_status) === "running";
export const canReviewTable = (run: Run) =>
  run.status === "ready" && !isTableTaskRunning(run);
export const isTextReady = (dataset?: Dataset) =>
  taskPhase(dataset?.status) === "completed";
export const hasRunningDatasets = (datasets: Dataset[]) =>
  datasets.some(isRunningTask);
export const taskColor = (phase: TaskPhase) =>
  ({
    waiting: "default",
    running: "processing",
    review: "processing",
    completed: "success",
    failed: "error",
  })[phase];

export function tableTaskPhase(run: Run): TaskPhase {
  if (isTableTaskRunning(run)) return "running";
  if (run.graph_status === "ready") return "completed";
  if (
    run.graph_status === "failed" ||
    run.status === "failed" ||
    run.result?.tables.some((table) => table.status === "failed")
  )
    return "failed";
  return taskPhase(run.status);
}
