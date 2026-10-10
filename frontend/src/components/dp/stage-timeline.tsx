"use client";

import clsx from "clsx";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { STAGE_LABELS, type StageRun } from "@/lib/dp";
import { formatBytes, formatDateTime, formatNumber, formatSeconds } from "@/lib/format";
import { StageIcon, StatusBadge } from "./status";

function progressLine(stage: StageRun) {
  const unit = stage.progress_unit ?? "records";
  const fmt = (n: number) => (unit === "bytes" ? formatBytes(n) : formatNumber(n));
  if (stage.progress_current == null) return null;
  const rate = stage.metrics?.rate_per_sec as number | undefined;
  const parts = [stage.progress_total ? `${fmt(stage.progress_current)} / ${fmt(stage.progress_total)}` : `${fmt(stage.progress_current)} ${unit}`];
  if (rate && stage.status === "RUNNING") parts.push(`${unit === "bytes" ? formatBytes(rate) : formatNumber(Math.round(rate))}/s`);
  return parts.join(" · ");
}

export function StageTimeline({ stages, onShowLogs }: { stages: StageRun[]; onShowLogs: (stage: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <ol className="relative">
      {stages.map((stage, index) => {
        const expanded = open === stage.stage || stage.status === "RUNNING" || stage.status === "FAILED" || stage.status === "RETRYING";
        const line = progressLine(stage);
        return (
          <li key={stage.id} className="relative flex gap-3 pb-3">
            {index < stages.length - 1 && <span className="absolute left-3 top-7 h-[calc(100%-1.25rem)] w-px bg-line-strong" aria-hidden />}
            <StageIcon status={stage.status} />
            <div className="min-w-0 flex-1">
              <button onClick={() => setOpen(open === stage.stage ? null : stage.stage)} className="flex w-full flex-wrap items-center gap-2 text-left">
                <span className="text-sm font-medium">{STAGE_LABELS[stage.stage] ?? stage.stage}</span>
                <StatusBadge status={stage.status} kind="stage" size="sm" />
                {stage.attempt > 1 && <span className="text-xs text-muted">attempt {stage.attempt}/{stage.max_attempts}</span>}
                <span className="ml-auto flex items-center gap-2 text-xs text-ink-2">
                  {stage.records_out != null && <span className="tabular">{formatNumber(stage.records_out)} out</span>}
                  {stage.duration_seconds != null && <span className="tabular">{formatSeconds(stage.duration_seconds)}</span>}
                  <ChevronDown size={14} className={clsx("text-muted transition-transform", expanded && "rotate-180")} />
                </span>
              </button>

              {stage.status === "RUNNING" && (
                <div className="mt-1.5">
                  {stage.progress_percent != null ? (
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={stage.progress_percent}>
                      <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${stage.progress_percent}%` }} />
                    </div>
                  ) : (
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-label="Total unknown">
                      <div className="h-full w-1/4 animate-pulse rounded-full bg-accent/50" />
                    </div>
                  )}
                  <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-ink-2">
                    {stage.progress_percent != null && <span className="tabular font-medium text-ink">{stage.progress_percent}%</span>}
                    {line && <span className="tabular">{line}</span>}
                    {stage.progress_message && <span className="text-muted">{stage.progress_message}</span>}
                  </div>
                </div>
              )}
              {stage.skip_reason && <p className="mt-0.5 text-xs text-muted">{stage.skip_reason}</p>}
              {stage.error_message && <p className="mt-1 rounded bg-critical-soft px-2 py-1 font-mono text-xs text-ink">{stage.error_message}</p>}

              {expanded && stage.status !== "PENDING" && (
                <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border border-line bg-surface-2/50 p-2.5 text-xs sm:grid-cols-4">
                  <Fact label="Started" value={formatDateTime(stage.started_at)} />
                  <Fact label="Finished" value={formatDateTime(stage.finished_at)} />
                  <Fact label="Records in" value={formatNumber(stage.records_in)} />
                  <Fact label="Records out" value={formatNumber(stage.records_out)} />
                  <Fact label="Rejected" value={formatNumber(stage.records_rejected)} />
                  <Fact label="Bytes" value={stage.bytes_processed != null ? formatBytes(stage.bytes_processed) : "–"} />
                  <Fact label="Throughput" value={stage.metrics?.records_per_sec ? `${formatNumber(Math.round(stage.metrics.records_per_sec as number))}/s` : "–"} />
                  <Fact label="Retries" value={String(Math.max(0, stage.attempt - 1))} />
                  {Object.entries(stage.metrics ?? {})
                    .filter(([k, v]) => !["records_per_sec", "rate_per_sec", "rate_unit", "duration_seconds", "header"].includes(k) && typeof v !== "object")
                    .slice(0, 8)
                    .map(([k, v]) => <Fact key={k} label={k.replaceAll("_", " ")} value={typeof v === "number" ? formatNumber(v) : String(v)} />)}
                  <div className="col-span-full">
                    <button onClick={() => onShowLogs(stage.stage)} className="text-accent-ink hover:underline">View logs for this stage →</button>
                  </div>
                </dl>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate capitalize text-muted">{label}</dt>
      <dd className="tabular truncate text-ink" title={value}>{value}</dd>
    </div>
  );
}
