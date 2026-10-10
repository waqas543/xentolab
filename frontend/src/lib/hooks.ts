"use client";

import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { FilterOptions } from "./types";

export function useFilterOptions() {
  return useQuery({
    queryKey: ["filter-options"],
    queryFn: () => api<FilterOptions>("/api/filters"),
    staleTime: 10 * 60_000,
  });
}

/** GET an API path; keeps the previous result on screen while refetching. */
export function useApi<T>(path: string | null) {
  return useQuery({
    queryKey: [path],
    queryFn: () => api<T>(path!),
    enabled: path !== null,
    placeholderData: keepPreviousData,
  });
}

export function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function withQuery(path: string, queryString: string, extra?: Record<string, string | number>) {
  const params = new URLSearchParams(queryString);
  for (const [key, value] of Object.entries(extra ?? {})) params.set(key, String(value));
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}
