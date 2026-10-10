"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { StatusBadge } from "@/components/dp/status";
import { Card, ErrorBox, Loading, PageHeader, Select, Spinner } from "@/components/ui";
import { dp, type PipelineDefinition, type Run } from "@/lib/dp";
import { formatDateTime, formatNumber, formatSeconds } from "@/lib/format";

const STATUSES = ["QUEUED", "DISCOVERING", "DOWNLOADING", "VALIDATING", "BRONZE_LOADING", "SILVER_PROCESSING",
  "ENTITY_RESOLUTION", "GOLD_PROCESSING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"];

export function RunsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const page = Number(params.get("page") ?? 1);
  const query = new URLSearchParams();
  for (const key of ["source", "pipeline", "status", "country", "trigger", "since", "until"]) {
    const value = params.get(key);
    if (value) query.set(key, key === "since" || key === "until" ? new Date(value).toISOString() : value);
  }
  query.set("page", String(page));
  query.set("page_size", "50");

  const pipelines = useQuery({ queryKey: ["dp-pipelines"], queryFn: () => dp<PipelineDefinition[]>("/pipelines") });
  const runs = useQuery({
    queryKey: ["dp-runs", query.toString()],
    queryFn: () => dp<{ total: number; rows: Run[] }>(`/runs?${query}`),
    refetchInterval: (q) => (q.state.data?.rows.some((r) => !r.is_terminal) ? 3000 : 15000),
  });
  const pages = runs.data ? Math.max(1, Math.ceil(runs.data.total / 50)) : 1;
  const sources = [...new Set((pipelines.data ?? []).map((p) => p.source_id))];

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader title="Pipeline runs" description="Every execution with its stages, record counts and errors. Updates live." />
      <div className="flex flex-wrap items-end gap-2">
        <Select label="Source" value={params.get("source") ?? ""} onChange={(v) => set("source", v)}
          options={[{ value: "", label: "All" }, ...sources.map((s) => ({ value: s, label: s }))]} />
        <Select label="Pipeline" value={params.get("pipeline") ?? ""} onChange={(v) => set("pipeline", v)}
          options={[{ value: "", label: "All" }, ...(pipelines.data ?? []).map((p) => ({ value: p.id, label: p.name }))]} />
        <Select label="Status" value={params.get("status") ?? ""} onChange={(v) => set("status", v)}
          options={[{ value: "", label: "All" }, ...STATUSES.map((s) => ({ value: s, label: s.replaceAll("_", " ").toLowerCase() }))]} />
        <Select label="Trigger" value={params.get("trigger") ?? ""} onChange={(v) => set("trigger", v)}
          options={["", "manual", "schedule", "retry", "backfill", "api", "cli"].map((s) => ({ value: s, label: s || "All" }))} />
        <label className="inline-flex items-center gap-1.5 text-xs text-ink-2">
          Country
          <input value={params.get("country") ?? ""} onChange={(e) => set("country", e.target.value.toUpperCase())} maxLength={2} placeholder="PK"
            className="h-8 w-14 rounded-md border border-line-strong bg-surface px-2 text-sm uppercase" />
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs text-ink-2">
          From
          <input type="date" value={params.get("since") ?? ""} onChange={(e) => set("since", e.target.value)}
            className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm" />
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs text-ink-2">
          To
          <input type="date" value={params.get("until") ?? ""} onChange={(e) => set("until", e.target.value)}
            className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm" />
        </label>
      </div>
      {runs.error && <ErrorBox error={runs.error} />}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-sm">
            <thead className="bg-surface-2 text-left text-xs text-ink-2">
              <tr>
                {["Run", "Source", "Dataset", "Country", "Status", "Started", "Duration", "Bronze", "Silver", "Gold", "Errors", "Triggered by"].map((h, i) => (
                  <th key={h} className={`px-3 py-2 font-medium ${i >= 7 && i <= 10 ? "text-right" : ""}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.data?.rows.map((run) => (
                <tr key={run.id} className="border-b border-line/60 last:border-0 hover:bg-surface-hover">
                  <td className="px-3 py-2">
                    <Link href={`/data-platform/runs/${run.id}`} className="font-medium text-accent-ink hover:underline">#{run.id}</Link>
                    {run.retry_of_run_id && <span className="ml-1 text-xs text-muted">retry of #{run.retry_of_run_id}</span>}
                  </td>
                  <td className="px-3 py-2">{run.source_id}</td>
                  <td className="max-w-48 truncate px-3 py-2 text-ink-2" title={run.dataset ?? ""}>{run.dataset ?? "–"}</td>
                  <td className="px-3 py-2">{run.country ?? "–"}</td>
                  <td className="px-3 py-2"><StatusBadge status={run.status} /></td>
                  <td className="whitespace-nowrap px-3 py-2 text-ink-2">{formatDateTime(run.started_at ?? run.created_at)}</td>
                  <td className="tabular px-3 py-2">{formatSeconds(run.duration_seconds)}</td>
                  <td className="tabular px-3 py-2 text-right">{formatNumber(run.bronze_records)}</td>
                  <td className="tabular px-3 py-2 text-right">{formatNumber(run.silver_records)}</td>
                  <td className="tabular px-3 py-2 text-right">{formatNumber(run.gold_records)}</td>
                  <td className={`tabular px-3 py-2 text-right ${run.error_count ? "font-medium text-critical" : "text-muted"}`}>{run.error_count}</td>
                  <td className="px-3 py-2 text-ink-2">{run.trigger_type}{run.triggered_by ? ` · ${run.triggered_by}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {runs.isLoading && <Loading />}
        {runs.data && !runs.data.rows.length && <p className="py-10 text-center text-sm text-muted">No runs match these filters</p>}
        <div className="flex items-center justify-end gap-2 border-t border-line px-3 py-2 text-sm text-ink-2">
          {runs.isFetching && <Spinner size={14} />}
          <span className="tabular">{formatNumber(runs.data?.total)} runs · page {page} of {pages}</span>
          <button aria-label="Previous page" disabled={page <= 1} onClick={() => set("page", String(page - 1))} className="rounded-md border border-line-strong p-1 disabled:opacity-40"><ChevronLeft size={16} /></button>
          <button aria-label="Next page" disabled={page >= pages} onClick={() => set("page", String(page + 1))} className="rounded-md border border-line-strong p-1 disabled:opacity-40"><ChevronRight size={16} /></button>
        </div>
      </Card>
    </div>
  );
}
