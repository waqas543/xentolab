"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Ban, RotateCw } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { StageTimeline } from "@/components/dp/stage-timeline";
import { StatusBadge } from "@/components/dp/status";
import { Button, Card, Empty, ErrorBox, Loading, Segmented } from "@/components/ui";
import {
  dp,
  dpPost,
  eventsUrl,
  getApiKey,
  STAGE_LABELS,
  type Run,
  type RunDetail,
} from "@/lib/dp";
import { formatBytes, formatClock, formatDateTime, formatNumber } from "@/lib/format";

type Tab = "timeline" | "logs" | "quality" | "errors" | "lineage" | "parameters";

/** Live run state: server-sent events, with polling as the fallback (e.g. when an API key header is needed). */
function useLiveRun(runId: number) {
  const client = useQueryClient();
  const [streaming, setStreaming] = useState(false);
  const query = useQuery({
    queryKey: ["dp-run", runId],
    queryFn: () => dp<RunDetail>(`/runs/${runId}`),
    refetchInterval: (q) => (streaming || q.state.data?.run.is_terminal ? false : 2000),
  });
  const terminal = query.data?.run.is_terminal;

  useEffect(() => {
    if (terminal || getApiKey() || typeof EventSource === "undefined") return; // EventSource cannot send headers
    const source = new EventSource(eventsUrl(runId));
    source.onopen = () => setStreaming(true);
    source.onmessage = (event) => {
      const update = JSON.parse(event.data) as RunDetail;
      client.setQueryData<RunDetail>(["dp-run", runId], (previous) => ({ ...previous, ...update }));
      if (update.run.is_terminal) client.invalidateQueries({ queryKey: ["dp-run", runId] });
    };
    source.addEventListener("end", () => source.close());
    source.onerror = () => {
      setStreaming(false);
      source.close();
    };
    return () => source.close();
  }, [runId, terminal, client]);

  return { ...query, streaming };
}

export function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = Number(params.id);
  const router = useRouter();
  const { data, error, isLoading, streaming, refetch } = useLiveRun(runId);
  const [tab, setTab] = useState<Tab>("timeline");
  const [logStage, setLogStage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (data?.run.is_terminal) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [data?.run.is_terminal]);

  if (isLoading) return <Loading />;
  if (error || !data) return <div className="p-6"><ErrorBox error={error ?? "Run not found"} /></div>;

  const { run, stages } = data;
  const elapsed = run.started_at ? ((run.finished_at ? new Date(run.finished_at).getTime() : now) - new Date(run.started_at).getTime()) / 1000 : null;
  const current = stages.find((s) => s.status === "RUNNING" || s.status === "RETRYING");

  const cancel = async () => {
    if (!confirm(`Cancel run #${run.id}? The current stage stops at its next checkpoint.`)) return;
    try {
      await dpPost(`/runs/${run.id}/cancel`, {});
      refetch();
    } catch (e) {
      setActionError(e);
    }
  };
  const retry = async () => {
    try {
      const next = await dpPost<Run>(`/runs/${run.id}/retry`, {});
      router.push(`/data-platform/runs/${next.id}`);
    } catch (e) {
      setActionError(e);
    }
  };

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-4 p-4 lg:p-6">
      <Link href="/data-platform/runs" className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <ArrowLeft size={14} /> All runs
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
            Run #{run.id} <StatusBadge status={run.status} />
            {streaming && !run.is_terminal && <span className="inline-flex items-center gap-1 text-xs font-normal text-good"><span className="size-1.5 animate-pulse rounded-full bg-[color:var(--good)]" /> live</span>}
          </h1>
          <p className="mt-0.5 text-sm text-ink-2">
            {run.pipeline_definition_id} · {run.country ?? "global"} · {run.dataset ?? ""} · {run.trigger_type}
            {run.triggered_by ? ` by ${run.triggered_by}` : ""}
            {run.retry_of_run_id && <> · retry of <Link className="text-accent-ink hover:underline" href={`/data-platform/runs/${run.retry_of_run_id}`}>#{run.retry_of_run_id}</Link></>}
          </p>
        </div>
        <div className="flex gap-2">
          {!run.is_terminal && (
            <Button variant="danger" onClick={cancel} disabled={!!run.cancel_requested_at}>
              <Ban size={14} /> {run.cancel_requested_at ? "Cancelling…" : "Cancel run"}
            </Button>
          )}
          {run.is_terminal && run.status !== "COMPLETED" && (
            <Button variant="primary" onClick={retry}><RotateCw size={14} /> Retry</Button>
          )}
        </div>
      </div>
      {actionError ? <ErrorBox error={actionError} /> : null}
      {run.error_summary && <ErrorBox error={run.error_summary} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        <Metric label="Current stage" value={current ? STAGE_LABELS[current.stage] : run.is_terminal ? "Finished" : "Waiting"} />
        <Metric label="Elapsed" value={formatClock(elapsed)} />
        <Metric label="Bronze" value={formatNumber(run.bronze_records)} />
        <Metric label="Silver" value={formatNumber(run.silver_records)} />
        <Metric label="Gold" value={formatNumber(run.gold_records)} />
        <Metric label="Downloaded" value={formatBytes(run.bytes_downloaded)} />
        <Metric label="Errors · warnings" value={`${run.error_count} · ${run.warning_count}`} tone={run.error_count ? "critical" : undefined} />
      </div>

      <Segmented
        value={tab}
        onChange={setTab}
        options={[
          { value: "timeline", label: "Timeline" },
          { value: "logs", label: "Logs" },
          { value: "quality", label: `Quality (${data.quality?.length ?? 0})` },
          { value: "errors", label: `Errors (${data.errors?.length ?? 0})` },
          { value: "lineage", label: "Lineage" },
          { value: "parameters", label: "Parameters" },
        ]}
      />

      <Card className="p-4">
        {tab === "timeline" && <StageTimeline stages={stages} onShowLogs={(stage) => { setLogStage(stage); setTab("logs"); }} />}
        {tab === "logs" && <Logs runId={run.id} stage={logStage} onStage={setLogStage} live={!run.is_terminal} stages={stages.map((s) => s.stage)} />}
        {tab === "quality" && <Quality results={data.quality ?? []} />}
        {tab === "errors" && <Errors errors={data.errors ?? []} />}
        {tab === "lineage" && <Lineage runId={run.id} />}
        {tab === "parameters" && (
          <div className="grid gap-4 md:grid-cols-2">
            <JsonBlock title="Parameters" value={run.parameters} />
            <JsonBlock title="Versions (reproducibility)" value={run.versions} />
            <JsonBlock title="Outputs" value={run.outputs} />
            {data.artifact && <JsonBlock title="Artifact" value={data.artifact} />}
            <div className="text-xs text-ink-2">
              Queued {formatDateTime(run.queued_at)} · started {formatDateTime(run.started_at)} · finished {formatDateTime(run.finished_at)}
              {run.worker_id && <> · worker {run.worker_id}</>}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "critical" }) {
  return (
    <Card className="px-3 py-2">
      <div className="text-[11px] font-medium text-muted">{label}</div>
      <div className={clsx("tabular truncate text-base font-semibold", tone === "critical" && "text-critical")}>{value}</div>
    </Card>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <pre className="max-h-80 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

interface LogLine { id: number; stage: string | null; level: string; message: string; context: Record<string, unknown>; created_at: string }

function Logs({ runId, stage, onStage, live, stages }: { runId: number; stage: string | null; onStage: (s: string | null) => void; live: boolean; stages: string[] }) {
  const { data, isLoading } = useQuery({
    queryKey: ["dp-logs", runId, stage],
    queryFn: () => dp<LogLine[]>(`/runs/${runId}/logs?limit=2000${stage ? `&stage=${stage}` : ""}`),
    refetchInterval: live ? 3000 : false,
  });
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1">
        {[null, ...stages].map((s) => (
          <button key={s ?? "all"} onClick={() => onStage(s)}
            className={clsx("rounded-full px-2 py-0.5 text-xs", s === stage ? "bg-accent text-white" : "bg-surface-2 text-ink-2 hover:text-ink")}>
            {s ? STAGE_LABELS[s] : "All stages"}
          </button>
        ))}
      </div>
      {isLoading && <Loading />}
      {data && !data.length && <Empty>No log lines</Empty>}
      <div className="max-h-[560px] overflow-auto rounded-lg bg-surface-2 p-2 font-mono text-xs leading-relaxed">
        {data?.map((line) => {
          const fields = Object.entries(line.context).filter(([k]) => !["source", "country", "dataset", "worker", "stage", "stage_run_id"].includes(k));
          return (
            <div key={line.id} className="flex gap-2 border-b border-line/40 py-0.5">
              <span className="shrink-0 text-muted">{new Date(line.created_at).toLocaleTimeString()}</span>
              <span className={clsx("w-12 shrink-0", line.level === "ERROR" ? "text-critical" : line.level === "WARNING" ? "text-ink" : "text-muted")}>{line.level}</span>
              <span className="shrink-0 text-ink-2">{line.stage ?? ""}</span>
              <span className="min-w-0 whitespace-pre-wrap break-words">
                {line.message}
                {fields.length > 0 && <span className="text-muted"> {fields.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ")}</span>}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Quality({ results }: { results: NonNullable<RunDetail["quality"]> }) {
  if (!results.length) return <Empty>No quality checks ran in this run (skipped stages reuse earlier results)</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-ink-2">
          <tr className="border-b border-line">
            {["Dataset", "Check", "Type", "Severity", "Status", "Checked", "Failed", "Failure %", "Threshold %"].map((h, i) => (
              <th key={h} className={clsx("px-2 py-1.5 font-medium", i >= 5 && "text-right")}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.id} className="border-b border-line/60" title={JSON.stringify(r.details)}>
              <td className="px-2 py-1.5 font-mono text-xs">{r.dataset_id}</td>
              <td className="px-2 py-1.5">{r.check_name}</td>
              <td className="px-2 py-1.5 text-ink-2">{r.check_type}</td>
              <td className="px-2 py-1.5 text-ink-2">{r.severity}</td>
              <td className="px-2 py-1.5"><QualityStatus status={r.status} /></td>
              <td className="tabular px-2 py-1.5 text-right">{formatNumber(r.records_checked)}</td>
              <td className="tabular px-2 py-1.5 text-right">{formatNumber(r.records_failed)}</td>
              <td className="tabular px-2 py-1.5 text-right">{r.failure_percentage ?? "–"}</td>
              <td className="tabular px-2 py-1.5 text-right">{r.threshold_percentage}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function QualityStatus({ status }: { status: string }) {
  const map: Record<string, string> = { passed: "SUCCEEDED", warned: "RETRYING", failed: "FAILED", error: "FAILED" };
  const labels: Record<string, string> = { passed: "Passed", warned: "Warning", failed: "Failed", error: "Check error" };
  return (
    <span className="inline-flex items-center gap-1">
      <StatusBadge status={map[status]} kind="stage" size="sm" />
      <span className="sr-only">{labels[status]}</span>
    </span>
  );
}

function Errors({ errors }: { errors: NonNullable<RunDetail["errors"]> }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!errors.length) return <Empty>No errors</Empty>;
  return (
    <ul className="flex flex-col gap-2">
      {errors.map((e) => (
        <li key={e.id} className="rounded-lg border border-line p-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono font-semibold text-critical">{e.exception_type}</span>
            <span className="text-ink-2">{e.stage ? STAGE_LABELS[e.stage] : "run"}{e.attempt ? ` · attempt ${e.attempt}` : ""}</span>
            <span className="rounded bg-surface-2 px-1.5 text-xs text-ink-2">{e.retryable ? "retryable" : "not retryable"}</span>
            <span className="ml-auto text-xs text-muted">{formatDateTime(e.created_at)}</span>
          </div>
          <p className="mt-1 whitespace-pre-wrap font-mono text-xs">{e.message}</p>
          {Object.keys(e.context).length > 0 && <pre className="mt-1 text-xs text-ink-2">{JSON.stringify(e.context)}</pre>}
          {e.traceback && (
            <button onClick={() => setOpen(open === e.id ? null : e.id)} className="mt-1 text-xs text-accent-ink hover:underline">
              {open === e.id ? "Hide" : "Show"} traceback
            </button>
          )}
          {open === e.id && <pre className="mt-1 max-h-72 overflow-auto rounded bg-surface-2 p-2 text-[11px]">{e.traceback}</pre>}
        </li>
      ))}
    </ul>
  );
}

interface LineageNode { id: string; kind: string; label: string; detail: Record<string, unknown> }

const LANES = ["artifact", "bronze", "silver", "entity_resolution", "gold"];
const LANE_LABELS: Record<string, string> = { artifact: "Artifact", bronze: "Bronze", silver: "Silver", entity_resolution: "Entity resolution", gold: "Gold" };

function Lineage({ runId }: { runId: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dp-lineage", runId],
    queryFn: () => dp<{ nodes: LineageNode[]; edges: { from: string; to: string }[] }>(`/runs/${runId}/lineage`),
  });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data?.nodes.length) return <Empty>This run produced no tracked outputs yet</Empty>;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-ink-2">Each box is an immutable artifact or dataset version; the flow runs left to right.</p>
      <div className="grid gap-3 overflow-x-auto md:grid-cols-5">
        {LANES.map((lane, index) => (
          <div key={lane} className="flex min-w-44 flex-col gap-2">
            <div className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-muted">
              {LANE_LABELS[lane]} {index < LANES.length - 1 && <span aria-hidden>→</span>}
            </div>
            {data.nodes.filter((n) => n.kind === lane).map((node) => (
              <div key={node.id} className="rounded-lg border border-line bg-surface-2/60 p-2 text-xs">
                <div className="font-mono font-medium text-ink">{node.label}</div>
                {Object.entries(node.detail).slice(0, 5).map(([k, v]) => (
                  <div key={k} className="truncate text-ink-2" title={JSON.stringify(v)}>
                    {k}: {typeof v === "number" ? formatNumber(v) : typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
