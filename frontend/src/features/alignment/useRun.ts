import { useEffect, useState } from "react";
import { alignmentApi } from "./api";
import type { Run } from "./types";

/** One request at a time; abort polling on selection changes and resume on refresh. */
export function useRun(token: string, id: string, revision: number) {
  const [run, setRun] = useState<Run>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setRun(undefined);
    setError("");
    if (!id) return () => controller.abort();
    async function poll() {
      try {
        const value = await alignmentApi.run(token, id, controller.signal);
        if (controller.signal.aborted) return;
        setRun(value);
        if (value.status === "analyzing" || value.graph_status === "building")
          timer = setTimeout(poll, 1500);
      } catch (reason) {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "读取任务失败");
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [token, id, revision]);
  return { run, error };
}
