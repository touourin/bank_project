import { useCallback, useState } from "react";
import { useResource } from "../../hooks/useResource";
import type { ConceptDetail } from "../alignment/types";

const noCandidates: ConceptDetail[] = [];

/** Start with retrieval evidence; cancel superseded searches and clean up their debounce. */
export function useConceptSearch(
  search: (query: string, signal: AbortSignal) => Promise<ConceptDetail[]>,
  initial = noCandidates,
  delay = 250,
) {
  const [query, setQuery] = useState<string | null>(null);
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) => {
        if (query === null && initial.length) return Promise.resolve(initial);
        return new Promise<ConceptDetail[]>((resolve, reject) => {
          const abort = () => {
            clearTimeout(timer);
            reject(new DOMException("Aborted", "AbortError"));
          };
          const timer = setTimeout(() => {
            signal.removeEventListener("abort", abort);
            if (!signal.aborted)
              search(query ?? "", signal).then(resolve, reject);
          }, delay);
          signal.addEventListener("abort", abort, { once: true });
          if (signal.aborted) abort();
        });
      },
      [query, initial, search, delay],
    ),
  );
  return {
    ...resource,
    search: setQuery,
    showingCandidates: query === null && initial.length > 0,
  };
}
