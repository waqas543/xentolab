"use client";

import clsx from "clsx";
import { AlertTriangle, Ban, CheckCircle2, CircleDashed, Clock, Loader2, MinusCircle, RotateCw, XCircle } from "lucide-react";
import type { RunStatus, StageStatus } from "@/lib/dp";

type Tone = "good" | "critical" | "warning" | "neutral" | "active";

const TONES: Record<Tone, string> = {
  good: "border-[color:var(--good)]/40 text-good bg-[color:var(--good)]/10",
  critical: "border-critical/40 text-critical bg-critical-soft",
  warning: "border-[color:var(--warning)]/50 text-ink bg-[color:var(--warning)]/15",
  neutral: "border-line-strong text-ink-2 bg-surface-2",
  active: "border-accent/40 text-accent-ink bg-accent-soft/40",
};

const RUN: Record<RunStatus, { tone: Tone; label: string }> = {
  PENDING: { tone: "neutral", label: "Pending" },
  QUEUED: { tone: "neutral", label: "Queued" },
  DISCOVERING: { tone: "active", label: "Discovering" },
  DOWNLOADING: { tone: "active", label: "Downloading" },
  VALIDATING: { tone: "active", label: "Validating" },
  BRONZE_LOADING: { tone: "active", label: "Bronze loading" },
  SILVER_PROCESSING: { tone: "active", label: "Silver processing" },
  ENTITY_RESOLUTION: { tone: "active", label: "Entity resolution" },
  GOLD_PROCESSING: { tone: "active", label: "Gold processing" },
  COMPLETED: { tone: "good", label: "Completed" },
  FAILED: { tone: "critical", label: "Failed" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
  PARTIALLY_COMPLETED: { tone: "warning", label: "Partially completed" },
};

const STAGE: Record<StageStatus, { tone: Tone; label: string }> = {
  PENDING: { tone: "neutral", label: "Pending" },
  RUNNING: { tone: "active", label: "Running" },
  RETRYING: { tone: "warning", label: "Retrying" },
  SUCCEEDED: { tone: "good", label: "Succeeded" },
  SKIPPED: { tone: "neutral", label: "Skipped" },
  FAILED: { tone: "critical", label: "Failed" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};

function icon(tone: Tone, status: string, size: number) {
  if (status === "RETRYING") return <RotateCw size={size} />;
  if (status === "SKIPPED") return <MinusCircle size={size} />;
  if (status === "CANCELLED") return <Ban size={size} />;
  if (status === "PENDING" || status === "QUEUED") return status === "QUEUED" ? <Clock size={size} /> : <CircleDashed size={size} />;
  if (tone === "active") return <Loader2 size={size} className="animate-spin" />;
  if (tone === "good") return <CheckCircle2 size={size} />;
  if (tone === "critical") return <XCircle size={size} />;
  return <AlertTriangle size={size} />;
}

export function StatusBadge({ status, kind = "run", size = "md" }: { status: string; kind?: "run" | "stage"; size?: "sm" | "md" }) {
  const meta = (kind === "run" ? RUN[status as RunStatus] : STAGE[status as StageStatus]) ?? { tone: "neutral" as Tone, label: status };
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border font-medium",
        size === "sm" ? "px-1.5 py-0 text-[11px]" : "px-2 py-0.5 text-xs",
        TONES[meta.tone],
      )}
    >
      {icon(meta.tone, status, size === "sm" ? 11 : 12)}
      {meta.label}
    </span>
  );
}

export function StageIcon({ status }: { status: StageStatus }) {
  const meta = STAGE[status];
  const color = { good: "text-good", critical: "text-critical", warning: "text-[color:var(--warning)]", neutral: "text-muted", active: "text-accent" }[meta.tone];
  return <span className={clsx("grid size-6 place-items-center rounded-full border border-line bg-surface", color)}>{icon(meta.tone, status, 14)}</span>;
}
