import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

// The next timer starts only after an answer arrives, so a slow engine never
// has two polls in flight from one view. An error keeps the last data, so a
// view can show the error beside it and a half-typed deny reason survives.
export function usePoll<T>(path: string, ms = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api<T>(path)
    .then((d) => { setData(d); setError(null); })
    .catch((e) => setError(e.message)), [path]);
  useEffect(() => {
    let stop = false, t: number | undefined;
    const tick = () => load().finally(() => { if (!stop) t = window.setTimeout(tick, ms); });
    tick();
    return () => { stop = true; clearTimeout(t); };
  }, [load, ms]);
  return { data, error, reload: load };
}
