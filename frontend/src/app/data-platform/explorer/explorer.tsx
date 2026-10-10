"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowDown, ArrowUp, Bookmark, ChevronLeft, ChevronRight, Columns3, Download, Lock, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { FilterBuilder, pruneIncomplete } from "@/components/dp/filter-builder";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, Segmented, Spinner } from "@/components/ui";
import {
  decodeFilters,
  dp,
  dpDownload,
  dpPost,
  encodeFilters,
  type DatasetInfo,
  type FilterField,
  type FilterGroup,
  type QueryResult,
} from "@/lib/dp";
import { formatBytes, formatNumber } from "@/lib/format";
import { useDebounced } from "@/lib/hooks";

const LAYERS = [
  { value: "gold", label: "Gold" },
  { value: "silver", label: "Silver" },
  { value: "bronze", label: "Bronze" },
  { value: "er", label: "Matches" },
] as const;

interface SavedView { id: number; name: string; dataset_id: string; query: { filters?: FilterGroup; search?: string; columns?: string[]; sort?: { field: string; direction: string }[] } }

function cell(value: unknown) {
  if (value === null || value === undefined) return <span className="text-muted">–</span>;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return <span className="font-mono text-xs">{JSON.stringify(value)}</span>;
  const text = String(value);
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

export function ExplorerPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const client = useQueryClient();

  const datasets = useQuery({ queryKey: ["dp-datasets"], queryFn: () => dp<DatasetInfo[]>("/datasets") });
  const datasetId = params.get("dataset") ?? "gold.businesses";
  const dataset = datasets.data?.find((d) => d.id === datasetId);
  const layer = datasetId.split(".")[0];

  const [filters, setFilters] = useState<FilterGroup>(() => decodeFilters(params.get("f")));
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [sort, setSort] = useState<{ field: string; direction: "asc" | "desc" } | null>(null);
  const [columns, setColumns] = useState<string[] | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selected, setSelected] = useState<string | null>(null);
  const [showColumns, setShowColumns] = useState(false);

  // Adopt URL changes made elsewhere (drill-down links, back/forward). Our own writes record their key first.
  const urlKey = `${datasetId}|${params.get("f") ?? ""}|${params.get("q") ?? ""}`;
  const [syncedUrl, setSyncedUrl] = useState(urlKey);
  if (syncedUrl !== urlKey) {
    setSyncedUrl(urlKey);
    setFilters(decodeFilters(params.get("f")));
    setSearch(params.get("q") ?? "");
    setSort(null);
    setColumns(null);
    setPage(1);
  }
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Keep filters in the URL so any view can be shared or bookmarked. */
  const writeUrl = (nextFilters: FilterGroup, nextSearch: string) => {
    const next = new URLSearchParams({ dataset: datasetId });
    const encoded = encodeFilters(pruneIncomplete(nextFilters));
    if (encoded) next.set("f", encoded);
    if (nextSearch) next.set("q", nextSearch);
    const key = `${datasetId}|${encoded}|${nextSearch}`;
    if (key !== urlKey) {
      setSyncedUrl(key);
      router.replace(`${pathname}?${next}`, { scroll: false });
    }
  };
  const changeFilters = (group: FilterGroup) => {
    setFilters(group);
    setPage(1);
    writeUrl(group, search);
  };
  const changeSearch = (value: string) => {
    setSearch(value);
    setPage(1);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => writeUrl(filters, value), 400);
  };

  const fields = useQuery({
    queryKey: ["dp-filters", datasetId],
    queryFn: () => dp<FilterField[]>(`/datasets/${datasetId}/filters`),
    enabled: !!dataset?.accessible,
  });

  const effective = useMemo(() => pruneIncomplete(filters), [filters]);
  const debouncedSearch = useDebounced(search, 350);
  const request = {
    filters: effective.conditions.length ? effective : null,
    search: debouncedSearch || null,
    columns,
    sort: sort ? [sort] : [],
    page,
    page_size: pageSize,
  };
  const result = useQuery({
    queryKey: ["dp-query", datasetId, JSON.stringify(request)],
    queryFn: () => dpPost<QueryResult>(`/datasets/${datasetId}/query`, request),
    enabled: !!dataset?.accessible,
    placeholderData: (previous) => previous,
  });

  const switchDataset = (id: string) => router.replace(`${pathname}?dataset=${id}`, { scroll: false });
  const pages = result.data ? Math.max(1, Math.ceil(result.data.total / pageSize)) : 1;
  const visibleColumns = result.data?.columns ?? [];
  const fieldMap = Object.fromEntries((fields.data ?? []).map((f) => [f.field, f]));
  const primaryIndex = visibleColumns.indexOf(
    { "gold.businesses": "gold_id", "silver.places": "silver_id", "gold.organizations": "organization_id", "gold.domains": "domain", "er.entity_matches": "id" }[datasetId] ?? "id",
  );

  const views = useQuery({ queryKey: ["dp-views", datasetId], queryFn: () => dp<SavedView[]>(`/views?dataset_id=${datasetId}`) });
  const saveView = useMutation({
    mutationFn: (name: string) => dpPost("/views", { name, dataset_id: datasetId, query: { ...request, page: 1 } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dp-views", datasetId] }),
  });
  const applyView = (view: SavedView) => {
    changeFilters(view.query.filters ?? { operator: "and", conditions: [] });
    setSearch(view.query.search ?? "");
    setColumns(view.query.columns ?? null);
    setSort((view.query.sort?.[0] as { field: string; direction: "asc" | "desc" }) ?? null);
    setPage(1);
  };

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader title="Data explorer" description="Query any Bronze, Silver or Gold dataset. Filtering, sorting and paging run on the server." />

      <div className="flex flex-wrap items-center gap-2">
        <Segmented value={layer} onChange={(l) => {
          const first = datasets.data?.find((d) => d.layer === l || d.id.startsWith(`${l}.`));
          if (first) switchDataset(first.id);
        }} options={LAYERS.map((l) => ({ ...l }))} />
        <div className="flex flex-wrap gap-1">
          {datasets.data?.filter((d) => d.id.startsWith(`${layer}.`)).map((d) => (
            <button key={d.id} onClick={() => switchDataset(d.id)}
              className={clsx("inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-sm",
                d.id === datasetId ? "border-accent bg-accent-soft/50 text-ink" : "border-line-strong text-ink-2 hover:bg-surface-hover")}>
              {!d.accessible && <Lock size={12} />}
              {d.label}
              {d.row_count != null && <span className="tabular text-xs text-muted">{formatNumber(d.row_count)}</span>}
            </button>
          ))}
        </div>
      </div>

      {dataset && !dataset.accessible && <ErrorBox error={`This dataset needs the ${dataset.id.startsWith("bronze") ? "data_platform.view_raw_data" : "required"} permission.`} />}
      {datasets.error && <ErrorBox error={datasets.error} />}

      {dataset?.accessible && (
        <>
          <Card className="flex flex-col gap-3 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <label className="relative flex h-8 min-w-56 flex-1 items-center sm:max-w-80">
                <Search size={14} className="pointer-events-none absolute left-2.5 text-muted" />
                <input value={search} onChange={(e) => changeSearch(e.target.value)} placeholder="Search…"
                  className="h-full w-full rounded-md border border-line-strong bg-surface pl-8 pr-2 text-sm outline-none focus:border-accent" />
              </label>
              <span className="text-xs text-muted">{dataset.description}{dataset.size_bytes ? ` · ${formatBytes(dataset.size_bytes)}` : ""}</span>
              <div className="ml-auto flex flex-wrap gap-1.5">
                {views.data && views.data.length > 0 && (
                  <select onChange={(e) => { const v = views.data!.find((x) => x.id === Number(e.target.value)); if (v) applyView(v); }} defaultValue=""
                    className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm">
                    <option value="" disabled>Saved views…</option>
                    {views.data.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                  </select>
                )}
                <Button size="md" onClick={() => { const name = prompt("Name this view"); if (name) saveView.mutate(name); }}>
                  <Bookmark size={14} /> Save view
                </Button>
                <Button onClick={() => setShowColumns(!showColumns)}><Columns3 size={14} /> Columns</Button>
                <Button onClick={() => dpDownload(`/datasets/${datasetId}/export`, { ...request, page: 1 }, `${datasetId}.csv`).catch((e) => alert(e.message))}>
                  <Download size={14} /> Export CSV
                </Button>
              </div>
            </div>
            {fields.data && (
              <FilterBuilder dataset={datasetId} fields={fields.data} value={filters} onChange={changeFilters} />
            )}
            {showColumns && fields.data && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-lg border border-line bg-surface-2/60 p-2">
                {fields.data.filter((f) => f.type !== "geo").map((f) => {
                  const active = columns ? columns.includes(f.field) : f.visible;
                  return (
                    <label key={f.field} className="inline-flex items-center gap-1.5 text-xs">
                      <input type="checkbox" checked={active} className="accent-[var(--accent)]"
                        onChange={() => {
                          const base = columns ?? fields.data!.filter((x) => x.visible && x.type !== "geo").map((x) => x.field);
                          setColumns(active ? base.filter((c) => c !== f.field) : [...base, f.field]);
                        }} />
                      {f.label}
                    </label>
                  );
                })}
              </div>
            )}
          </Card>

          {result.error && <ErrorBox error={result.error} />}
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className={clsx("w-full text-sm transition-opacity", result.isFetching && "opacity-60")}>
                <thead className="bg-surface-2 text-left text-xs text-ink-2">
                  <tr>
                    {visibleColumns.map((name) => {
                      const field = fieldMap[name];
                      return (
                        <th key={name} className="whitespace-nowrap px-3 py-2 font-medium">
                          {field?.sortable ? (
                            <button className="inline-flex items-center gap-1 hover:text-ink"
                              onClick={() => setSort(sort?.field === name ? { field: name, direction: sort.direction === "asc" ? "desc" : "asc" } : { field: name, direction: "asc" })}>
                              {field.label}
                              {sort?.field === name && (sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
                            </button>
                          ) : (field?.label ?? name)}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {result.data?.rows.map((row, i) => (
                    <tr key={i} onClick={() => primaryIndex >= 0 && setSelected(String(row[primaryIndex]))}
                      className={clsx("border-b border-line/60 last:border-0", primaryIndex >= 0 && "cursor-pointer hover:bg-surface-hover")}>
                      {row.map((value, j) => <td key={j} className="max-w-72 truncate px-3 py-1.5" dir="auto">{cell(value)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result.isLoading && <Loading />}
            {result.data && !result.data.rows.length && <Empty>No records match</Empty>}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-2 text-sm text-ink-2">
              <label className="inline-flex items-center gap-1.5 text-xs">
                Rows
                <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} className="h-7 rounded border border-line-strong bg-surface px-1">
                  {[25, 50, 100, 200, 500].map((n) => <option key={n}>{n}</option>)}
                </select>
              </label>
              <div className="flex items-center gap-2">
                {result.isFetching && <Spinner size={14} />}
                <span className="tabular">{formatNumber(result.data?.total)} records · page {page} of {formatNumber(pages)}</span>
                <button aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-md border border-line-strong p-1 disabled:opacity-40"><ChevronLeft size={16} /></button>
                <button aria-label="Next page" disabled={page >= pages} onClick={() => setPage(page + 1)} className="rounded-md border border-line-strong p-1 disabled:opacity-40"><ChevronRight size={16} /></button>
              </div>
            </div>
          </Card>
        </>
      )}
      {selected && <RecordDrawer datasetId={datasetId} recordKey={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

interface Provenance {
  sources: { silver_id: string; source: string; source_record_id: string; is_primary: boolean; match_confidence: number;
    artifact_id: number | null; pipeline_run_id: number | null; transformation_version: string | null;
    silver: Record<string, unknown> | null; artifact: { filename: string; source_version: string | null; sha256: string } | null }[];
  matches: { id: number; left_id: string; right_id: string; status: string; match_score: number; name_score: number | null;
    geo_score: number | null; phone_score: number | null; domain_score: number | null; distance_m: number | null; algorithm_version: string;
    evidence: Record<string, unknown>; decided_by: string | null }[];
}

interface OrganizationDetail {
  organization: Record<string, unknown> | null;
  records: { silver_id: string; source: string; source_record_id: string; link_method: string; confidence: number | null;
    source_url: string | null; pipeline_run_id: number | null; silver: Record<string, unknown> | null }[];
  locations: { gold_id: string; name: string; city: string | null; sources: string[] }[];
}

const LINK_METHODS: Record<string, string> = {
  wikidata_id: "a location carries its Wikidata id", domain: "same website domain", name_geo: "same name, close by",
  name: "same name", identifier: "Wikidata lists this registry id", registry_search: "found by searching its legal name",
  standalone: "not linked to a location yet",
};

function OrganizationSection({ detail }: { detail: OrganizationDetail }) {
  const org = detail.organization ?? {};
  return (
    <section className="mb-5">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Organization{org.name ? `: ${String(org.name)}` : ""} · {detail.records.length} linked record{detail.records.length === 1 ? "" : "s"}
      </h3>
      {org.description ? <p className="mb-2 text-sm text-ink-2">{String(org.description)}</p> : null}
      <ul className="flex flex-col gap-2">
        {detail.records.map((r) => (
          <li key={r.silver_id} className="rounded-lg border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{r.source}</Badge>
              {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer" className="font-mono text-xs text-accent-ink hover:underline">{r.source_record_id}</a>
                : <span className="font-mono text-xs">{r.source_record_id}</span>}
              <span className="ml-auto text-xs text-muted">{LINK_METHODS[r.link_method] ?? r.link_method}</span>
            </div>
            {r.silver && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-accent-ink">Source record</summary>
                <pre className="mt-1 max-h-64 overflow-auto rounded bg-surface-2 p-2 text-[11px]">{JSON.stringify(r.silver, null, 2)}</pre>
              </details>
            )}
          </li>
        ))}
      </ul>
      {detail.locations.length > 0 && (
        <p className="mt-2 text-xs text-ink-2">Locations: {detail.locations.map((l) => `${l.name}${l.city ? ` (${l.city})` : ""}`).join(" · ")}</p>
      )}
    </section>
  );
}

function RecordDrawer({ datasetId, recordKey, onClose }: { datasetId: string; recordKey: string; onClose: () => void }) {
  const client = useQueryClient();
  const { data, error, isLoading } = useQuery({
    queryKey: ["dp-record", datasetId, recordKey],
    queryFn: () => dp<{ record: Record<string, unknown>; provenance?: Provenance; organization?: OrganizationDetail }>(
      `/datasets/${datasetId}/records/${encodeURIComponent(recordKey)}`),
  });
  const decide = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => dpPost(`/matches/${id}/decision`, { status }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dp-record", datasetId, recordKey] }),
  });

  return (
    <div className="fixed inset-0 z-[1300] flex justify-end" role="dialog" aria-modal aria-label="Record details">
      <button aria-label="Close" className="absolute inset-0 bg-black/25" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-2xl flex-col overflow-hidden border-l border-line bg-surface shadow-card">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div>
            <div className="text-xs text-muted">{datasetId}</div>
            <h2 className="text-lg font-semibold">{String(data?.record?.name ?? recordKey)}</h2>
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-surface-hover"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading && <Loading />}
          {error && <ErrorBox error={error} />}
          {data?.organization && <OrganizationSection detail={data.organization} />}
          {data?.provenance && (
            <section className="mb-5">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Where this came from ({data.provenance.sources.length} source records)</h3>
              <ul className="flex flex-col gap-2">
                {data.provenance.sources.map((s) => (
                  <li key={s.silver_id} className="rounded-lg border border-line p-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>{s.source}</Badge>
                      <span className="font-mono text-xs">{s.source_record_id}</span>
                      {s.is_primary && <Badge className="border-accent/40 text-accent-ink">primary</Badge>}
                      <span className="ml-auto text-xs text-muted">confidence {s.match_confidence.toFixed(2)}</span>
                    </div>
                    <div className="mt-1 text-xs text-ink-2">
                      Artifact {s.artifact?.filename ?? s.artifact_id} ({s.artifact?.source_version ?? "?"}) ·
                      run <Link className="text-accent-ink hover:underline" href={`/data-platform/runs/${s.pipeline_run_id}`}>#{s.pipeline_run_id}</Link> · {s.transformation_version}
                    </div>
                    {s.silver && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-accent-ink">Source record & raw tags</summary>
                        <pre className="mt-1 max-h-64 overflow-auto rounded bg-surface-2 p-2 text-[11px]">{JSON.stringify(s.silver, null, 2)}</pre>
                      </details>
                    )}
                  </li>
                ))}
              </ul>
              {data.provenance.matches.length > 0 && (
                <>
                  <h3 className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-muted">Match evidence</h3>
                  <ul className="flex flex-col gap-2">
                    {data.provenance.matches.map((m) => (
                      <li key={m.id} className="rounded-lg border border-line p-3 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono">{m.left_id} ↔ {m.right_id}</span>
                          <Badge>{m.status}{m.decided_by ? ` by ${m.decided_by}` : ""}</Badge>
                          <span className="ml-auto tabular">score {m.match_score.toFixed(2)}</span>
                        </div>
                        <div className="mt-1 tabular text-ink-2">
                          name {m.name_score?.toFixed(2) ?? "–"} · geo {m.geo_score?.toFixed(2) ?? "–"} · {m.distance_m != null ? `${Math.round(m.distance_m)} m` : "–"}
                          {m.phone_score != null && ` · phone ${m.phone_score}`}{m.domain_score != null && ` · domain ${m.domain_score}`} · {m.algorithm_version}
                        </div>
                        <div className="mt-2 flex gap-1.5">
                          <Button size="sm" onClick={() => decide.mutate({ id: m.id, status: "accepted" })} disabled={m.status === "accepted"}>Accept</Button>
                          <Button size="sm" onClick={() => decide.mutate({ id: m.id, status: "rejected" })} disabled={m.status === "rejected"}>Reject</Button>
                          <span className="self-center text-muted">Applied on the next Gold build.</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          )}
          {data && (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Record</h3>
              <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1 text-sm">
                {Object.entries(data.record).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="font-mono text-xs text-muted">{k}</dt>
                    <dd className="min-w-0 break-words" dir="auto">{typeof v === "object" && v !== null ? <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(v, null, 2)}</pre> : cell(v)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
