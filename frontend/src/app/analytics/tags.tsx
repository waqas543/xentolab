"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { BarList } from "@/components/charts/bar-list";
import { ChartCard } from "@/components/charts/chart-card";
import { Card, ErrorBox, Loading, Spinner } from "@/components/ui";
import { useFilters, useUrlParam } from "@/lib/filters";
import { formatNumber } from "@/lib/format";
import { useApi, withQuery } from "@/lib/hooks";
import type { Count } from "@/lib/types";

export function TagsTab() {
  const { queryString } = useFilters();
  const [key, setKey] = useUrlParam("tag", "cuisine");
  const [search, setSearch] = useState("");

  const keys = useApi<Count[]>(withQuery("/api/stats/tags", queryString, { limit: 1000 }));
  const values = useApi<Count[]>(key ? withQuery(`/api/stats/tags/${encodeURIComponent(key)}`, queryString, { limit: 50 }) : null);

  const visibleKeys = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (keys.data ?? []).filter((k) => String(k.label).toLowerCase().includes(term));
  }, [keys.data, search]);

  const total = keys.data?.find((k) => k.label === "name")?.count ?? 0;

  return (
    <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
      <Card className="flex max-h-[720px] flex-col overflow-hidden">
        <div className="border-b border-line px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            Tag keys {keys.isFetching && <Spinner size={13} />}
          </h2>
          <p className="text-xs text-muted">How many companies carry each OSM tag</p>
          <label className="relative mt-2 flex items-center">
            <Search size={14} className="pointer-events-none absolute left-2.5 text-muted" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter keys…"
              className="h-8 w-full rounded-md border border-line-strong bg-surface pl-8 text-sm outline-none focus:border-accent"
            />
          </label>
        </div>
        {keys.error && <ErrorBox error={keys.error} className="m-3" />}
        {keys.isLoading && <Loading />}
        <ul className="flex-1 overflow-auto py-1">
          {visibleKeys.map((k) => (
            <li key={k.label}>
              <button
                onClick={() => setKey(String(k.label))}
                aria-current={k.label === key}
                className="flex w-full items-center justify-between gap-2 px-4 py-1 text-left text-sm hover:bg-surface-hover aria-[current=true]:bg-accent-soft/50"
              >
                <span className="truncate font-mono text-xs">{k.label}</span>
                <span className="tabular text-xs text-ink-2">
                  {formatNumber(k.count)}
                  {total > 0 && <span className="ml-1.5 inline-block w-11 text-right text-muted">{((k.count / total) * 100).toFixed(1)}%</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <ChartCard
        title={`Values of “${key}”`}
        subtitle="Top 50 values among the filtered companies"
        loading={values.isFetching}
        chart={
          values.error ? (
            <ErrorBox error={values.error} />
          ) : (
            <BarList items={(values.data ?? []).map((v) => ({ key: String(v.label), label: <span dir="auto">{v.label}</span>, value: v.count }))} />
          )
        }
      />
    </div>
  );
}
