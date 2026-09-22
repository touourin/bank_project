import { useCallback, useEffect, useState } from "react";
import { useTaskResource } from "../../hooks/useTaskResource";
import {
  hasRunningDatasets,
  isTextReady,
  isRunningTask,
} from "../conversion/taskState";
import { knowledgeApi } from "./api";

export const isDatasetReady = isTextReady;
export const isDatasetRunning = isRunningTask;

export function useTextDatasets(token: string, active: boolean) {
  const datasets = useTaskResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.datasets(token, signal),
      [token],
    ),
    { isRunning: hasRunningDatasets, interval: 2500 },
  );
  const [selected, select] = useState("");
  const { refresh } = datasets;
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);
  const dataset =
    datasets.data?.find((item) => item.key === selected) ?? datasets.data?.[0];
  return { ...datasets, dataset, select };
}
