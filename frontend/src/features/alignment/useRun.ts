import { useCallback, useEffect } from "react";
import { useTaskResource } from "../../hooks/useTaskResource";
import { isTableTaskRunning } from "../conversion/taskState";
import { alignmentApi } from "./api";

/** One request at a time; abort polling on selection changes and resume on refresh. */
export function useRun(token: string, id: string, revision: number) {
  const task = useTaskResource(
    useCallback(
      (signal: AbortSignal) =>
        id ? alignmentApi.run(token, id, signal) : Promise.resolve(null),
      [token, id],
    ),
    { isRunning: isTableTaskRunning },
  );
  const { refresh } = task;
  useEffect(refresh, [revision, refresh]);
  return { run: task.data, error: task.error ?? "" };
}
