import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "../api/request";

type Options<T> = {
  isRunning: (value: T) => boolean;
  interval?: number;
  revision?: (value: T) => number;
};

/** Poll after a response, never overlap requests or roll back a confirmed revision. */
export function useTaskResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  { isRunning, interval = 1500, revision }: Options<T>,
) {
  const [state, setState] = useState<{
    load: typeof load;
    data?: T;
    error?: string;
    loading: boolean;
  }>({ load, loading: true });
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(() => setGeneration((value) => value + 1), []);
  const accepted = useRef<{ load: typeof load; data: T } | undefined>(
    undefined,
  );
  const accept = useCallback(
    (value: T) => {
      const current = accepted.current;
      const data =
        current?.load === load &&
        revision &&
        revision(current.data) > revision(value)
          ? current.data
          : value;
      accepted.current = { load, data };
      setState({ load, data, loading: false });
      return data;
    },
    [load, revision],
  );
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setState((previous) => ({
      load,
      data: previous.load === load ? previous.data : undefined,
      loading: true,
    }));
    async function poll() {
      try {
        const value = await load(controller.signal);
        if (controller.signal.aborted) return;
        const data = accept(value);
        if (isRunning(data)) timer = setTimeout(poll, interval);
      } catch (reason) {
        if (!controller.signal.aborted)
          setState((previous) => ({
            ...previous,
            load,
            error: errorMessage(reason),
            loading: false,
          }));
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [load, accept, isRunning, interval, generation]);
  // Hide the previous selection immediately, before the effect cleans up its request.
  return {
    ...(state.load === load
      ? state
      : { data: undefined, loading: true, error: undefined }),
    refresh,
    accept,
  };
}
