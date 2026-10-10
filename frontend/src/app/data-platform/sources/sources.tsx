"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Database, ExternalLink, History, KeyRound, Play, Settings2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ParamForm } from "@/components/dp/param-form";
import { StatusBadge } from "@/components/dp/status";
import { Badge, Button, Card, ErrorBox, Loading, PageHeader, Select } from "@/components/ui";
import { dp, dpPatch, dpPost, explorerHref, type PipelineDefinition, type Run, type Source } from "@/lib/dp";
import { formatBytes, formatDateTime, formatNumber, timeAgo } from "@/lib/format";

// Where each source's normalized output lives (the "View data" link).
const SOURCE_DATASET: Record<string, string> = {
  wikidata: "silver.organizations",
  opencorporates: "silver.organizations",
  commoncrawl: "silver.web_domains",
  gdelt: "silver.org_mentions",
  geonames: "silver.geo_places",
};

export function SourcesPage() {
  const sources = useQuery({ queryKey: ["dp-sources"], queryFn: () => dp<Source[]>("/sources"), refetchInterval: 10_000 });
  const [running, setRunning] = useState<Source | null>(null);
  const [configuring, setConfiguring] = useState<Source | null>(null);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader title="Data sources" description="Pluggable source adapters. Each runs independent pipelines into Bronze, Silver and Gold." />
      {sources.error && <ErrorBox error={sources.error} />}
      {sources.isLoading && <Loading />}
      <div className="grid gap-4 lg:grid-cols-2">
        {sources.data?.map((source) => (
          <SourceCard key={source.id} source={source} onRun={() => setRunning(source)} onConfigure={() => setConfiguring(source)} />
        ))}
      </div>
      <p className="text-xs text-muted">
        New providers plug in as adapters under <code>backend/data_platform/sources/&lt;id&gt;/</code> — see docs/data-platform/adding-a-source.md.
      </p>
      {running && <RunDialog source={running} onClose={() => setRunning(null)} />}
      {configuring && <ConfigureDialog source={configuring} onClose={() => setConfiguring(null)} />}
    </div>
  );
}

function SourceCard({ source, onRun, onConfigure }: { source: Source; onRun: () => void; onConfigure: () => void }) {
  const client = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => dpPatch(`/sources/${source.id}`, { enabled: !source.enabled }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dp-sources"] }),
  });
  const missing = (source.credentials ?? []).filter((c) => !c.configured);
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">{source.name}</h2>
            {source.status ? <StatusBadge status={source.status} /> : <Badge>never run</Badge>}
            {!source.enabled && <Badge className="text-critical">disabled</Badge>}
            {source.credentials?.map((c) => (
              <Badge key={c.name} className={c.configured ? "text-good" : "border-warning bg-warning/15"}>
                <KeyRound size={11} className="mr-1 inline" />{c.configured ? "credentials set" : "needs credentials"}
              </Badge>
            ))}
          </div>
          <p className="mt-0.5 text-xs text-ink-2">{source.description}</p>
          <p className="mt-0.5 text-[11px] text-muted">
            {source.license} {source.homepage && <a href={source.homepage} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 hover:underline">· {new URL(source.homepage).host} <ExternalLink size={10} /></a>}
          </p>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <Fact label="Last success" value={source.last_success ? timeAgo(source.last_success.finished_at) : "–"} title={formatDateTime(source.last_success?.finished_at)} />
        <Fact label="Last failure" value={source.last_failure ? timeAgo(source.last_failure.finished_at) : "–"} title={source.last_failure?.error_summary ?? ""} tone={source.last_failure ? "critical" : undefined} />
        <Fact label="Latest dataset version" value={source.latest_artifact?.source_version?.slice(0, 19).replace("T", " ") ?? "–"} />
        <Fact label="Latest artifact" value={source.latest_artifact ? `${source.latest_artifact.filename} (${formatBytes(source.latest_artifact.size_bytes)})` : "–"} />
        <Fact label="Records (Bronze · Silver)" value={`${formatNumber(source.records.bronze)} · ${formatNumber(source.records.silver)}`} />
        <Fact label="Storage" value={formatBytes(source.storage_bytes)} />
        <Fact label="Schedule" value={source.schedules.length ? source.schedules.map((s) => `${s.name} (${s.cadence})`).join(", ") : "Manual"} />
        <Fact label="Pipelines" value={source.pipelines.map((p) => p.name).join(", ")} />
        <Fact label="Capabilities" value={Object.entries(source.capabilities).filter(([, v]) => v === true).map(([k]) => k.replace("supports_", "").replace("provides_", "")).join(", ")} />
      </dl>
      {missing.length > 0 && (
        <p className="rounded-md bg-warning/10 px-2.5 py-1.5 text-xs text-ink-2">
          Set {missing.map((c) => <code key={c.name} className="font-mono">{c.name}</code>)} in the worker&apos;s environment (e.g. <code>.env</code>) and restart <code>make up</code>.
          Credentials are never entered here or stored by the platform.
        </p>
      )}
      <div className="flex flex-wrap gap-2 border-t border-line pt-3">
        <Button variant="primary" onClick={onRun} disabled={!source.enabled || !source.registered || missing.length > 0}
          title={missing.length ? "Credentials missing" : undefined}><Play size={14} /> Run</Button>
        <Button onClick={onConfigure}><Settings2 size={14} /> Configure</Button>
        <Link href={`/data-platform/runs?source=${source.id}`} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-3 text-sm font-medium hover:bg-surface-hover"><History size={14} /> View runs</Link>
        <Link href={explorerHref(SOURCE_DATASET[source.id] ?? "silver.places", { operator: "and", conditions: [{ field: "source", operator: "in", value: [source.id] }] })}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-3 text-sm font-medium hover:bg-surface-hover"><Database size={14} /> View data</Link>
        <Button variant="ghost" onClick={() => toggle.mutate()}>{source.enabled ? "Disable" : "Enable"}</Button>
      </div>
    </Card>
  );
}

function Fact({ label, value, title, tone }: { label: string; value: string; title?: string; tone?: "critical" }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className={`truncate ${tone === "critical" ? "text-critical" : ""}`} title={title || value}>{value}</dd>
    </div>
  );
}

function defaults(definition: PipelineDefinition) {
  const values: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(definition.parameter_schema.properties)) if ("default" in spec) values[key] = spec.default;
  return { ...values, ...definition.default_parameters };
}

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-[1300] grid place-items-center bg-black/30 p-4" role="dialog" aria-modal aria-label={title}>
      <Card className="flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 hover:bg-surface-hover"><X size={16} /></button>
        </div>
        <div className="overflow-y-auto p-4">{children}</div>
      </Card>
    </div>
  );
}

function RunDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const router = useRouter();
  const [pipelineId, setPipelineId] = useState(source.pipelines[0]?.id);
  const definition = source.pipelines.find((p) => p.id === pipelineId)!;
  const [values, setValues] = useState<Record<string, unknown>>(() => defaults(definition));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const run = await dpPost<Run>(`/pipelines/${pipelineId}/runs`, { parameters: values });
      router.push(`/data-platform/runs/${run.id}`);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <Dialog title={`Run ${source.name}`} onClose={onClose}>
      {source.pipelines.length > 1 && (
        <Select label="Pipeline" value={pipelineId} onChange={(id) => { setPipelineId(id); setValues(defaults(source.pipelines.find((p) => p.id === id)!)); }}
          options={source.pipelines.map((p) => ({ value: p.id, label: p.name }))} className="mb-3" />
      )}
      <p className="mb-3 text-xs text-ink-2">{definition.description}</p>
      <ParamForm definition={definition} values={values} onChange={setValues} />
      {error ? <ErrorBox error={error} className="mt-3" /> : null}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={busy}><Play size={14} /> Queue run</Button>
      </div>
      <p className="mt-2 text-[11px] text-muted">The run is queued and executed by a worker (<code>make data-platform-worker</code>). This page returns immediately.</p>
    </Dialog>
  );
}

function ConfigureDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const client = useQueryClient();
  const definition = source.pipelines[0];
  const [cadence, setCadence] = useState("weekly");
  const [cron, setCron] = useState("");
  const [name, setName] = useState(`${source.name} weekly`);
  const [values, setValues] = useState<Record<string, unknown>>(() => defaults(definition));
  const [error, setError] = useState<unknown>(null);
  const create = useMutation({
    mutationFn: () => dpPost("/schedules", { pipeline_definition_id: definition.id, name, parameters: values, cadence, cron: cron || null }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dp-sources"] }),
    onError: setError,
  });
  const toggle = useMutation({
    mutationFn: (id: number) => dpPatch(`/schedules/${id}`, { enabled: false }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dp-sources"] }),
  });

  return (
    <Dialog title={`Configure ${source.name}`} onClose={onClose}>
      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted"><CalendarClock size={13} /> Schedules</h3>
      {source.schedules.length === 0 && <p className="text-sm text-ink-2">No schedules — runs are manual.</p>}
      <ul className="mb-4 flex flex-col gap-1">
        {source.schedules.map((s) => (
          <li key={s.id} className="flex items-center justify-between rounded border border-line px-2 py-1 text-sm">
            <span>{s.name} · {s.cadence}{s.cron ? ` (${s.cron})` : ""} · next {formatDateTime(s.next_run_at)}</span>
            <Button size="sm" variant="ghost" onClick={() => toggle.mutate(s.id)}>Disable</Button>
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-2">Name
            <input value={name} onChange={(e) => setName(e.target.value)} className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm" />
          </label>
          <Select label="Cadence" value={cadence} onChange={setCadence}
            options={["daily", "weekly", "monthly", "on_release", "cron"].map((c) => ({ value: c, label: c === "on_release" ? "when a new release is available" : c }))} />
          {cadence === "cron" && (
            <label className="flex flex-col gap-1 text-xs font-medium text-ink-2">Cron
              <input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 3 * * 1" className="h-8 rounded-md border border-line-strong bg-surface px-2 font-mono text-sm" />
            </label>
          )}
        </div>
        <ParamForm definition={definition} values={values} onChange={setValues} />
        {error ? <ErrorBox error={error} /> : null}
        <div className="flex justify-end"><Button variant="primary" onClick={() => create.mutate()}>Add schedule</Button></div>
      </div>
    </Dialog>
  );
}
