"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

export const LIST_KEYS = ["group", "category", "province", "district", "city", "brand"] as const;
export const FLAG_KEYS = ["has_phone", "has_website", "has_email", "has_address", "has_opening_hours"] as const;

export type ListKey = (typeof LIST_KEYS)[number];
export type FlagKey = (typeof FLAG_KEYS)[number];

export type Filters = {
  q?: string;
  min_completeness?: number;
  branded?: boolean;
} & Record<ListKey, string[]> &
  Partial<Record<FlagKey, boolean>>;

export const FLAG_LABELS: Record<FlagKey, string> = {
  has_phone: "Phone",
  has_website: "Website",
  has_email: "Email",
  has_address: "Address",
  has_opening_hours: "Opening hours",
};

export const LIST_LABELS: Record<ListKey, string> = {
  group: "Group",
  category: "Category",
  province: "Province",
  district: "District",
  city: "City",
  brand: "Brand",
};

function parseBool(value: string | null) {
  return value === "true" ? true : value === "false" ? false : undefined;
}

export function parseFilters(params: URLSearchParams): Filters {
  const filters = { q: params.get("q") ?? undefined } as Filters;
  for (const key of LIST_KEYS) filters[key] = params.getAll(key);
  for (const key of FLAG_KEYS) filters[key] = parseBool(params.get(key));
  const min = params.get("min_completeness");
  filters.min_completeness = min ? Number(min) : undefined;
  filters.branded = parseBool(params.get("branded"));
  return filters;
}

/** Serialize filters to a query string the API (and the page URL) understands. */
export function filtersToParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  for (const key of LIST_KEYS) for (const value of filters[key] ?? []) params.append(key, value);
  for (const key of FLAG_KEYS) if (filters[key] !== undefined) params.set(key, String(filters[key]));
  if (filters.min_completeness) params.set("min_completeness", String(filters.min_completeness));
  if (filters.branded !== undefined) params.set("branded", String(filters.branded));
  return params;
}

export function activeFilterCount(filters: Filters) {
  let count = filters.q ? 1 : 0;
  for (const key of LIST_KEYS) count += filters[key].length;
  for (const key of FLAG_KEYS) if (filters[key] !== undefined) count += 1;
  if (filters.min_completeness) count += 1;
  if (filters.branded !== undefined) count += 1;
  return count;
}

/** Filters live in the URL, so every page shares them and views can be bookmarked. */
export function useFilters() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters = useMemo(() => parseFilters(new URLSearchParams(searchParams.toString())), [searchParams]);
  const queryString = useMemo(() => filtersToParams(filters).toString(), [filters]);

  const setFilters = useCallback(
    (patch: Partial<Filters>) => {
      const next = filtersToParams({ ...filters, ...patch });
      // Keep non-filter params (e.g. the active analytics tab).
      for (const [key, value] of new URLSearchParams(searchParams.toString())) {
        if (key !== "q" && key !== "min_completeness" && key !== "branded" &&
            !(LIST_KEYS as readonly string[]).includes(key) && !(FLAG_KEYS as readonly string[]).includes(key)) {
          next.append(key, value);
        }
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [filters, pathname, router, searchParams],
  );

  const clearFilters = useCallback(() => {
    setFilters({
      q: undefined,
      min_completeness: undefined,
      branded: undefined,
      ...Object.fromEntries(LIST_KEYS.map((k) => [k, []])),
      ...Object.fromEntries(FLAG_KEYS.map((k) => [k, undefined])),
    } as Partial<Filters>);
  }, [setFilters]);

  return { filters, queryString, setFilters, clearFilters };
}

/** Read/write a single non-filter URL param (tabs, dimension pickers). */
export function useUrlParam(name: string, fallback: string): [string, (value: string) => void] {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const value = searchParams.get(name) ?? fallback;

  const setValue = useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === fallback) params.delete(name);
      else params.set(name, next);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [fallback, name, pathname, router, searchParams],
  );

  return [value, setValue];
}
