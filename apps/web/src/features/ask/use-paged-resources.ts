"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";

export type ResourcePage<T> = { items: T[]; next_cursor?: string | null };
export function usePagedResources<T extends { id: string }>(path: string, query = "") {
  const [items, setItems] = useState<T[]>([]), [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const dataKey = path + "?q=" + query.trim();
  const [loadedKey, setLoadedKey] = useState("");
  const version = useRef(0), reading = useRef(false);
  const invalidate = useCallback(() => { ++version.current; reading.current = false; }, []);
  const read = useCallback(async (before?: string, identifier?: string) => {
    if (before && reading.current) return [];
    const current = ++version.current;
    reading.current = true; setLoading(true); setError("");
    const params = new URLSearchParams();
    if (identifier) params.set("identifier", identifier);
    else if (query.trim()) params.set("q", query.trim());
    if (before) params.set("before", before);
    try {
      const result = await api<ResourcePage<T>>(path + (params.size ? `?${params}` : ""));
      if (current === version.current) {
        setLoadedKey(dataKey);
        setItems((previous) => identifier || before
          ? [...previous.filter((item) => !result.items.some((next) => next.id === item.id)), ...result.items]
          : result.items);
        if (!identifier) setCursor(result.next_cursor || null);
      }
      return result.items;
    } catch (reason) {
      if (current === version.current) {
        setError((reason as Error).message);
        if (!before && !identifier) { setLoadedKey(dataKey); setItems([]); setCursor(null); }
      }
      throw reason;
    }
    finally { if (current === version.current) { reading.current = false; setLoading(false); } }
  }, [path, query, dataKey]);
  useEffect(() => {
    const timer = setTimeout(() => { void read().catch(() => {}); }, query ? 250 : 0);
    return () => { clearTimeout(timer); invalidate(); };
  }, [read, query, invalidate]);
  const current = loadedKey === dataKey;
  return { items: current ? items : [], setItems, cursor: current ? cursor : null, loading: loading || !current,
    error: current ? error : "", reload: (identifier?: string) => read(undefined, identifier),
    more: () => current && cursor && !loading ? read(cursor) : Promise.resolve([] as T[]) };
}
