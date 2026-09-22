import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../api/request";

type Callbacks<T> = {
  onSuccess?: (value: T) => void;
  onError?: () => void;
  onSettled?: () => void;
};

/** Serialize user actions and ignore completions after the owning workspace unmounts. */
export function useAsyncAction() {
  const [pending, setPending] = useState<string>();
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const epoch = useRef(0);
  useEffect(() => {
    epoch.current += 1;
    return () => {
      epoch.current += 1;
    };
  }, []);

  async function execute<T>(
    name: string,
    action: () => Promise<T>,
    callbacks: Callbacks<T> = {},
  ) {
    if (inFlight.current) return;
    inFlight.current = true;
    const started = epoch.current;
    setPending(name);
    setError("");
    try {
      const result = await action();
      if (epoch.current === started) callbacks.onSuccess?.(result);
    } catch (reason) {
      if (epoch.current === started) {
        setError(errorMessage(reason));
        callbacks.onError?.();
      }
    } finally {
      inFlight.current = false;
      if (epoch.current === started) {
        setPending(undefined);
        callbacks.onSettled?.();
      }
    }
  }
  return {
    execute,
    pending,
    busy: Boolean(pending),
    error,
    clearError: () => setError(""),
  };
}
