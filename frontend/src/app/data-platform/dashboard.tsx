"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ExternalLink, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarList } from "@/components/charts/bar-list";
import { ChartCard, SimpleTable } from "@/components/charts/chart-card";
import { DensityMap, type View } from "@/components/dp/density-map";
import { FacetPicker, FilterBuilder, pruneIncomplete } from "@/components/dp/filter-builder";
import { StatTile } from "@/components/stat-tile";
import { Card, Empty, ErrorBox, PageHeader, Segmented, Spinner } from "@/components/ui";
import { StatusBadge } from "@/components/dp/status";
import { decodeFilters, dp, encodeFilters, explorerHref, isGroup, STAGE_LABELS, type Condition, type FilterField, type FilterGroup, type RunStatus } from "@/lib/dp";
import { formatBytes, formatCompact, formatDateTime, formatNumber, formatPercent, formatSeconds, humanize, timeAgo } from "@/lib/format";

const TABS = ["overview", "sources", "organizations", "geography", "categories", "domains", "quality", "operations"] as const;
type Tab = (typeof TABS)[number];

const QUICK_FILTERS: { field: string; label: string }[] = [
  { field: "country_code", label: "Country" },
  { field: "admin1", label: "Region" },
  { field: "city", label: "City" },
  { field: "sources", label: "Source" },
  { field: "category_group", label: "Category group" },
  { field: "category", label: "Category" },
  { field: "entity_type", label: "Entity type" },
  { field: "gold_version_id", label: "Dataset version" },
];

/** Global filter state lives in the URL (?f=), shared with drill-down links into the explorer. */
function useGlobalFilters() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters = decodeFilters(params.get("f"));
  const setFilters = (group: FilterGroup) => {
    const next = new URLSearchParams(params.toString());
    const encoded = encodeFilters(pruneIncomplete(group));
    if (encoded) next.set("f", encoded);
    else next.delete("f");
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const valuesFor = (field: string) => {
    const condition = filters.conditions.find((c) => !isGroup(c) && c.field === field) as Condition | undefined;
    return (condition && Array.isArray(condition.value) ? condition.value : condition?.value != null ? [condition.value] : []) as string[];
  };
  const setValues = (field: string, values: unknown[]) => {
    const rest = filters.conditions.filter((c) => isGroup(c) || c.field !== field);
    setFilters({ operator: "and", conditions: values.length ? [...rest, { field, operator: "in", value: values }] : rest });
  };
  const toggle = (field: string, value: string) => {
    const current = valuesFor(field);
    setValues(field, current.includes(value) ? current.filter((v) => v !== value) : [...current, value]);
  };
  return { filters, setFilters, valuesFor, setValues, toggle, query: encodeFilters(filters) };
}

function useDash<T>(endpoint: string, query: string, extra = "") {
  const path = `/dashboard/${endpoint}?${query ? `filters=${encodeURIComponent(query)}&` : ""}${extra}`;
  return useQuery({ queryKey: ["dp-dash", path], queryFn: () => dp<T>(path), placeholderData: (p) => p });
}

export function DashboardPage() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") as Tab) ?? "overview";
  const global = useGlobalFilters();
  const [advanced, setAdvanced] = useState(false);
  const fields = useQuery({ queryKey: ["dp-filters", "gold.businesses"], queryFn: () => dp<FilterField[]>("/datasets/gold.businesses/filters") });
  const fieldMap = Object.fromEntries((fields.data ?? []).map((f) => [f.field, f]));
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params.toString());
    next.set("tab", t);
    router.replace(`?${next}`, { scroll: false });
  };
  const dateCondition = global.filters.conditions.find((c) => !isGroup(c) && c.field === "source_updated_at") as Condition | undefined;
  const dateRange = (dateCondition?.value as string[] | undefined) ?? ["", ""];
  const setDate = (index: number, value: string) => {
    const range = [...dateRange];
    range[index] = value;
    const rest = global.filters.conditions.filter((c) => isGroup(c) || c.field !== "source_updated_at");
    global.setFilters({ operator: "and", conditions: range[0] && range[1] ? [...rest, { field: "source_updated_at", operator: "between", value: range }] : rest });
  };

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader title="Data platform" description="Coverage, quality and operations across every source. All widgets react to the filters; click to drill down."
        actions={<Segmented value={tab} onChange={setTab} options={TABS.map((t) => ({ value: t, label: humanize(t) }))} />} />

      {tab !== "operations" && (
        <Card className="flex flex-col gap-2 p-3">
          <div className="flex flex-wrap items-start gap-2">
            {QUICK_FILTERS.filter((q) => fieldMap[q.field]).map((q) => (
              <div key={q.field} className="w-44">
                <div className="mb-0.5 text-[11px] font-medium text-muted">{q.label}</div>
                <FacetPicker dataset="gold.businesses" field={fieldMap[q.field]} multiple value={global.valuesFor(q.field)} onChange={(v) => global.setValues(q.field, v as unknown[])} />
              </div>
            ))}
            <div>
              <div className="mb-0.5 text-[11px] font-medium text-muted">Source updated</div>
              <div className="flex gap-1">
                <input type="date" value={dateRange[0]?.slice(0, 10) ?? ""} onChange={(e) => setDate(0, e.target.value)} className="h-8 rounded-md border border-line-strong bg-surface px-1.5 text-sm" />
                <input type="date" value={dateRange[1]?.slice(0, 10) ?? ""} onChange={(e) => setDate(1, e.target.value)} className="h-8 rounded-md border border-line-strong bg-surface px-1.5 text-sm" />
              </div>
            </div>
            <div className="ml-auto flex items-end gap-1 self-end">
              <button onClick={() => setAdvanced(!advanced)} className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-sm text-ink-2 hover:bg-surface-hover">
                <SlidersHorizontal size={14} /> Advanced
              </button>
              {global.filters.conditions.length > 0 && (
                <button onClick={() => global.setFilters({ operator: "and", conditions: [] })} className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-sm text-accent-ink hover:underline">
                  <X size={14} /> Clear
                </button>
              )}
              <Link href={explorerHref("gold.businesses", global.filters)} className="inline-flex h-8 items-center gap-1 rounded-md border border-line-strong px-2 text-sm hover:bg-surface-hover">
                Open in explorer <ExternalLink size={12} />
              </Link>
            </div>
          </div>
          {advanced && fields.data && <FilterBuilder dataset="gold.businesses" fields={fields.data} value={global.filters} onChange={global.setFilters} />}
        </Card>
      )}

      {tab === "overview" && <Overview g={global} />}
      {tab === "sources" && <Sources g={global} />}
      {tab === "organizations" && <Organizations g={global} />}
      {tab === "geography" && <Geography g={global} />}
      {tab === "categories" && <Categories g={global} />}
      {tab === "domains" && <Domains g={global} />}
      {tab === "quality" && <Quality g={global} />}
      {tab === "operations" && <Operations />}
    </div>
  );
}

type Global = ReturnType<typeof useGlobalFilters>;
type Row = Record<string, number | string | null>;

function Drill({ g, extra, children, dataset = "gold.businesses" }: { g: Global; extra: Condition[]; children: React.ReactNode; dataset?: string }) {
  return (
    <Link href={explorerHref(dataset, g.filters, extra)} className="block rounded-xl transition-shadow hover:ring-2 hover:ring-accent/40" title="Open these records in the explorer">
      {children}
    </Link>
  );
}

function pct(part: number | null | undefined, whole: number | null | undefined) {
  return formatPercent(part ?? 0, whole ?? 0);
}

// ---------------------------------------------------------------- overview

interface Contribution {
  source: string; name: string; role: string; status: string | null; last_finished: string | null;
  missing_credentials: string[]; silver: Record<string, number>;
  businesses_found: number; businesses_enriched: number; organizations: number; domains: number;
}

interface OverviewData {
  kpis: Record<string, number | string | null>;
  layers: Record<string, { rows: number; bytes: number }>;
  by_source: { source: string; businesses: number }[];
  contributions: Contribution[];
}

// The source's main Silver dataset (what "records" links to).
const MAIN_SILVER: Record<string, string> = {
  wikidata: "silver.organizations", opencorporates: "silver.organizations", commoncrawl: "silver.web_domains",
  gdelt: "silver.org_mentions", geonames: "silver.geo_places",
};

function ContributionTable({ g, rows }: { g: Global; rows: Contribution[] }) {
  const count = (n: number, link?: string) => (n ? (link ? <Link href={link} className="hover:underline">{formatNumber(n)}</Link> : formatNumber(n)) : <span className="text-muted">–</span>);
  const only = (source: string): FilterGroup => ({ operator: "and", conditions: [{ field: "source", operator: "in", value: [source] }] });
  return (
    <SimpleTable
      columns={[
        { key: "source", label: "Source" }, { key: "role", label: "Contributes" }, { key: "records", label: "Silver records", numeric: true },
        { key: "found", label: "Businesses found", numeric: true }, { key: "enriched", label: "Businesses enriched", numeric: true },
        { key: "orgs", label: "Organizations", numeric: true }, { key: "domains", label: "Domains", numeric: true }, { key: "status", label: "Last run" },
      ]}
      rows={rows.map((c) => {
        const records = Object.values(c.silver).reduce((a, b) => a + b, 0);
        const dataset = MAIN_SILVER[c.source] ?? "silver.places";
        return {
          source: <span className="font-medium">{c.name}</span>,
          role: <span className="text-ink-2">{c.role}</span>,
          records: count(records, explorerHref(dataset, only(c.source))),
          found: count(c.businesses_found, explorerHref("gold.businesses", g.filters, [{ field: "sources", operator: "contains", value: c.source }])),
          enriched: count(c.businesses_enriched, explorerHref("gold.businesses", g.filters, [{ field: "enriched_by", operator: "contains", value: c.source }])),
          orgs: count(c.organizations, explorerHref("gold.organizations", { operator: "and", conditions: [] }, [{ field: "sources", operator: "contains", value: c.source }])),
          domains: count(c.domains, explorerHref("gold.domains", { operator: "and", conditions: [] }, [{ field: "sources", operator: "contains", value: c.source }])),
          status: c.missing_credentials.length
            ? <Link href="/data-platform/sources" className="text-xs text-ink-2 underline decoration-warning decoration-2">needs credentials</Link>
            : c.status ? <span title={formatDateTime(c.last_finished)}><StatusBadge status={c.status as RunStatus} /> <span className="text-xs text-muted">{timeAgo(c.last_finished)}</span></span>
              : <span className="text-xs text-muted">never run</span>,
        };
      })} />
  );
}

function Overview({ g }: { g: Global }) {
  const { data, error, isFetching } = useDash<OverviewData>("overview", g.query);
  const k = data?.kpis ?? {};
  const n = (key: string) => (k[key] as number) ?? 0;
  const total = n("businesses");
  if (error) return <ErrorBox error={error} />;
  return (
    <div className={clsx("flex flex-col gap-4 transition-opacity", isFetching && "opacity-70")}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <StatTile label="Raw records (Bronze)" value={formatCompact(n("raw_records"))} detail="latest artifact per source" />
        <StatTile label="Silver records" value={formatNumber(n("silver_records"))} detail="normalized observations" />
        <Drill g={g} extra={[]}><StatTile label="Unique Gold businesses" value={formatNumber(total)} detail="after entity resolution" /></Drill>
        <StatTile label="Unique companies" value={formatNumber(n("organizations"))} detail="brands / multi-location orgs" />
        <StatTile label="Unique locations" value={formatNumber(n("locations"))} detail="distinct coordinates (~10 m)" />
        <Drill g={g} extra={[{ field: "domain", operator: "is_not_null" }]}><StatTile label="Unique domains" value={formatNumber(n("domains"))} detail="own websites, not platforms" /></Drill>
        <Drill g={g} extra={[{ field: "has_website", operator: "equals", value: true }]}><StatTile label="With website" value={formatNumber(n("with_website"))} detail={pct(n("with_website"), total)} /></Drill>
        <Drill g={g} extra={[{ field: "has_website", operator: "equals", value: false }]}><StatTile label="Without website" value={formatNumber(total - n("with_website"))} detail={pct(total - n("with_website"), total)} /></Drill>
        <Drill g={g} extra={[{ field: "has_phone", operator: "equals", value: true }]}><StatTile label="With phone" value={formatNumber(n("with_phone"))} detail={pct(n("with_phone"), total)} /></Drill>
        <Drill g={g} extra={[{ field: "has_address", operator: "equals", value: true }]}><StatTile label="With address" value={formatNumber(n("with_address"))} detail={pct(n("with_address"), total)} /></Drill>
        <StatTile label="Countries · cities" value={`${formatNumber(n("countries"))} · ${formatNumber(n("cities"))}`} detail="covered" />
        <StatTile label="Sources active" value={formatNumber(n("sources_active"))} detail={`pipeline success ${k.pipeline_success_rate ?? "–"}% (30 d)`} />
        <StatTile label="Storage used" value={formatBytes(n("storage_bytes"))} detail="artifacts + dataset versions" />
        <StatTile label="Data freshness" value={timeAgo(k.latest_release as string)} detail={`latest release ${formatDateTime(k.latest_release as string)}`} />
        <StatTile label="Avg. completeness" value={k.avg_completeness != null ? `${k.avg_completeness}%` : "–"} detail="website, phone, email, address" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Records per layer" subtitle="Bronze → Silver → Gold (from the dataset catalog)"
          chart={<BarList items={Object.entries(data?.layers ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([id, v]) => ({ key: id, label: id, value: v.rows, hint: formatBytes(v.bytes) }))} />} />
        <ChartCard title="Gold businesses by location source" subtitle="Sources that find places. A business found by several counts for each; the other sources enrich them (below)."
          chart={<BarList items={(data?.by_source ?? []).map((s) => ({ key: s.source, label: s.source, value: s.businesses }))} onSelect={(s) => g.toggle("sources", s)} selected={g.valuesFor("sources")} total={total} />} />
        <ChartCard className="lg:col-span-2" title="What each source contributes"
          subtitle="Location sources find businesses; Wikidata, Common Crawl, GDELT and OpenCorporates enrich them and add organizations, domains and news. Numbers open in the explorer."
          chart={data?.contributions?.length ? <ContributionTable g={g} rows={data.contributions} /> : <Empty>No sources registered</Empty>} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- sources

interface Overlap {
  combinations: { sources: string[]; businesses: number; website_pct: number; phone_pct: number; email_pct: number }[];
  enrichment: { field: string; by: string; primary_source: string; businesses: number }[];
  per_source: { source: string; businesses: number; only_this_source: number; shared: number }[];
  enriched_by: { source: string; by: string; businesses: number }[];
  enriched_totals: { by: string; businesses: number }[];
}

const ENRICHER_NOTES: Record<string, string> = {
  wikidata: "its organization or the place itself is a Wikidata item",
  opencorporates: "its organization has a registry record",
  gdelt: "its organization is named in the news",
  commoncrawl: "its website is in the web crawl",
};

function Sources({ g }: { g: Global }) {
  const { data, error } = useDash<{ sources: Row[]; by_source_count: { source_count: number; businesses: number }[] }>("sources", g.query);
  const overlap = useDash<Overlap>("overlap", g.query);
  if (error) return <ErrorBox error={error} />;
  const rows = data?.sources ?? [];
  const combos = overlap.data?.combinations ?? [];
  const totalBusinesses = combos.reduce((sum, c) => sum + c.businesses, 0);
  const comboLabel = (sources: string[]) => (sources.length === 1 ? `Only ${sources[0]}` : sources.join(" + "));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ChartCard title="Source overlap" subtitle="Which sources found each Gold business — click to open them in the explorer"
        chart={
          <ul className="flex flex-col">
            {combos.map((c) => (
              <li key={c.sources.join("+")}>
                <Link href={explorerHref("gold.businesses", g.filters, [{ field: "source_count", operator: "equals", value: c.sources.length },
                  ...c.sources.map((s) => ({ field: "sources", operator: "contains", value: s }))])}
                  className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 rounded-md px-1.5 py-1.5 hover:bg-surface-hover">
                  <span className="truncate text-sm">{comboLabel(c.sources)} <span className="text-xs text-muted">web {c.website_pct}% · phone {c.phone_pct}%</span></span>
                  <span className="tabular text-sm text-ink-2">{formatNumber(c.businesses)} <span className="text-xs text-muted">{pct(c.businesses, totalBusinesses)}</span></span>
                  <span className="col-span-2 h-1.5 rounded-full bg-surface-2">
                    <span className="block h-full rounded-full bg-accent" style={{ width: `${(c.businesses / Math.max(1, combos[0]?.businesses ?? 1)) * 100}%` }} />
                  </span>
                </Link>
              </li>
            ))}
            {!combos.length && <Empty>No Gold businesses yet</Empty>}
          </ul>
        } />
      <ChartCard title="Enrichment between sources" subtitle="Fields one source added to a business whose name came from another"
        chart={
          overlap.data?.enrichment.length ? (
            <SimpleTable columns={[{ key: "field", label: "Field" }, { key: "by", label: "Supplied by" }, { key: "primary", label: "Business from" },
              { key: "n", label: "Businesses", numeric: true }]}
              rows={overlap.data.enrichment.map((e) => ({ field: humanize(e.field), by: e.by, primary: e.primary_source, n: formatNumber(e.businesses) }))} />
          ) : <Empty>Needs a second source for the same country</Empty>
        } />
      <ChartCard title="Enriched by reference sources" subtitle="Businesses described by a source that is not a location record — click to open them"
        chart={
          overlap.data?.enriched_totals.length ? (
            <BarList items={overlap.data.enriched_totals.map((e) => ({ key: e.by, label: e.by, value: e.businesses, hint: ENRICHER_NOTES[e.by] }))}
              total={totalBusinesses} />
          ) : <Empty>Run Wikidata, Common Crawl, GDELT or OpenCorporates for this country</Empty>
        } />
      <ChartCard title="Who enriches whom" subtitle="e.g. how many OSM businesses Wikidata describes"
        chart={
          overlap.data?.enriched_by.length ? (
            <SimpleTable columns={[{ key: "source", label: "Business from" }, { key: "by", label: "Enriched by" }, { key: "n", label: "Businesses", numeric: true }]}
              rows={overlap.data.enriched_by.map((e) => ({ source: e.source, by: e.by, n: formatNumber(e.businesses) }))} />
          ) : <Empty>No enrichment yet</Empty>
        } />
      <ChartCard className="lg:col-span-2" title="Source coverage" subtitle="What each source contributes to Gold, and how much it overlaps with others"
        chart={
          <SimpleTable columns={[
            { key: "source", label: "Source" }, { key: "bronze", label: "Bronze records", numeric: true }, { key: "silver", label: "Silver", numeric: true },
            { key: "gold", label: "Gold contributions", numeric: true }, { key: "unique", label: "Only this source", numeric: true },
            { key: "overlap", label: "Overlap rate", numeric: true }, { key: "web", label: "Website", numeric: true },
            { key: "phone", label: "Phone", numeric: true }, { key: "geo", label: "Cities", numeric: true }]}
            rows={rows.map((r) => ({ source: r.source, bronze: formatNumber(r.bronze_records as number), silver: formatNumber(r.silver_records as number),
              gold: formatNumber(r.gold_contributions as number), unique: formatNumber(r.unique_contributions as number), overlap: `${r.overlap_rate ?? 0}%`,
              web: `${r.website_pct}%`, phone: `${r.phone_pct}%`, geo: formatNumber(r.cities as number) }))} />
        } />
      <ChartCard title="Discovered by N sources" subtitle="Businesses confirmed by more independent sources are more trustworthy"
        chart={<BarList items={(data?.by_source_count ?? []).map((r) => ({ key: String(r.source_count), label: `${r.source_count} source${r.source_count === 1 ? "" : "s"}`, value: r.businesses }))} />} />
      <ChartCard title="Website coverage by source" chart={<BarList max={100} items={rows.map((r) => ({ key: String(r.source), label: String(r.source), value: Number(r.website_pct), display: `${r.website_pct}%` }))} />} />
    </div>
  );
}

// ---------------------------------------------------------------- organizations

interface OrgRow { organization_id: string; name: string; location_count: number; city_count: number; sources: string[]; wikidata_id: string | null;
  registry_id: string | null; news_mentions: number; industry: string | null; news_tone?: number | null }
interface Orgs {
  kpis: Record<string, number>;
  by_source: { label: string; organizations: number; with_locations: number }[];
  by_category: { label: string; organizations: number; with_locations: number }[];
  top_located: OrgRow[];
  top_news: OrgRow[];
  links: { method: string; status: string; links: number }[];
  unmatched_news: { name_key: string; name: string; mentions: number; articles: number; last_seen: string }[];
}

const NONE: FilterGroup = { operator: "and", conditions: [] };

function orgHref(id: string) {
  return explorerHref("gold.organizations", NONE, [{ field: "organization_id", operator: "equals", value: id }]);
}

function Organizations({ g }: { g: Global }) {
  const { data, error, isFetching } = useDash<Orgs>("organizations", g.query);
  if (error) return <ErrorBox error={error} />;
  const k = data?.kpis ?? {};
  const orgTable = (rows: OrgRow[], news: boolean) => (
    <ul className="flex flex-col">
      {rows.map((o) => (
        <li key={o.organization_id}>
          <Link href={orgHref(o.organization_id)} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 rounded-md px-1.5 py-1 hover:bg-surface-hover">
            <span className="truncate text-sm">{o.name} <span className="text-xs text-muted">{o.sources.join(" · ")}</span></span>
            <span className="tabular text-sm text-ink-2">
              {news ? `${formatNumber(o.news_mentions)} mentions` : `${formatNumber(o.location_count)} locations`}
              <span className="ml-1 text-xs text-muted">{news ? `${formatNumber(o.location_count)} loc.` : `${formatNumber(o.city_count)} cities`}</span>
            </span>
          </Link>
        </li>
      ))}
      {!rows.length && <Empty>None yet</Empty>}
    </ul>
  );
  return (
    <div className={clsx("flex flex-col gap-4 transition-opacity", isFetching && "opacity-70")}>
      <p className="text-xs text-muted">Organizations merge brands and shared websites seen at locations with Wikidata items and registry records. Only the country filter applies here.</p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Link href={explorerHref("gold.organizations", NONE)} className="block rounded-xl hover:ring-2 hover:ring-accent/40"><StatTile label="Organizations" value={formatNumber(k.organizations)} /></Link>
        <Link href={explorerHref("gold.organizations", NONE, [{ field: "location_count", operator: "greater_than", value: 0 }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="With locations" value={formatNumber(k.with_locations)} detail={`${formatNumber(k.multi_location)} with 2+`} />
        </Link>
        <Link href={explorerHref("gold.organizations", NONE, [{ field: "location_count", operator: "equals", value: 0 }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="Known only from records" value={formatNumber(k.records_only)} detail="Wikidata / registry, no location yet" />
        </Link>
        <Link href={explorerHref("gold.organizations", NONE, [{ field: "wikidata_id", operator: "is_not_null" }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="With Wikidata" value={formatNumber(k.with_wikidata)} detail={`${formatNumber(k.located_with_wikidata)} also have locations`} />
        </Link>
        <Link href={explorerHref("gold.organizations", NONE, [{ field: "registry_id", operator: "is_not_null" }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="With registry record" value={formatNumber(k.with_registry)} detail="OpenCorporates" />
        </Link>
        <Link href={explorerHref("gold.organizations", NONE, [{ field: "news_mentions", operator: "greater_than", value: 0 }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="In the news" value={formatNumber(k.in_news)} detail={`${formatNumber(k.news_mentions)} mentions (GDELT)`} />
        </Link>
        <Link href={explorerHref("er.entity_matches", NONE, [{ field: "match_type", operator: "in", value: ["organization"] }, { field: "status", operator: "in", value: ["suggested"] }])}
          className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="Links to review" value={formatNumber((data?.links ?? []).filter((l) => l.status === "suggested").reduce((a, l) => a + l.links, 0))} detail="suggested organization links" />
        </Link>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Largest organizations" subtitle="By number of resolved locations" chart={orgTable(data?.top_located ?? [], false)} />
        <ChartCard title="Most in the news" subtitle="GDELT mentions in news about the country" chart={orgTable(data?.top_news ?? [], true)} />
        <ChartCard title="Organizations by source" subtitle="An organization described by several sources counts for each"
          chart={<BarList items={(data?.by_source ?? []).map((r) => ({ key: r.label, label: r.label, value: r.organizations, hint: `${formatNumber(r.with_locations)} with locations` }))} />} />
        <ChartCard title="By category" chart={<BarList items={(data?.by_category ?? []).map((r) => ({ key: r.label, label: r.label, value: r.organizations, hint: `${formatNumber(r.with_locations)} with locations` }))} />} />
        <ChartCard title="How records were linked" subtitle="Entity resolution between places, Wikidata and registries"
          chart={<SimpleTable columns={[{ key: "method", label: "Evidence" }, { key: "status", label: "Status" }, { key: "n", label: "Links", numeric: true }]}
            rows={(data?.links ?? []).map((l) => ({ method: humanize(l.method ?? "unknown"), status: humanize(l.status), n: formatNumber(l.links) }))} />} />
        <ChartCard title="In the news, not in Gold" subtitle="Frequently mentioned organizations no source has linked yet — discovery leads"
          chart={
            data?.unmatched_news.length ? (
              <ul className="flex flex-col">
                {data.unmatched_news.map((m) => (
                  <li key={m.name_key}>
                    <Link href={explorerHref("silver.org_mentions", NONE, [{ field: "name_key", operator: "equals", value: m.name_key }])}
                      className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 rounded-md px-1.5 py-1 hover:bg-surface-hover">
                      <span className="truncate text-sm">{m.name}</span>
                      <span className="tabular text-sm text-ink-2">{formatNumber(m.mentions)} <span className="text-xs text-muted">{formatNumber(m.articles)} articles</span></span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : <Empty>Run GDELT to see news mentions</Empty>
          } />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- geography

interface Geo { by_country: { label: string; count: number }[]; by_region: { label: string; count: number }[]; by_city: { label: string; parent: string; count: number }[]; grid: { cells: { latitude: number; longitude: number; count: number }[] } }

function Geography({ g }: { g: Global }) {
  const [view, setView] = useState<View | null>(null);
  const extra = view ? `zoom=${view.zoom}&west=${view.west.toFixed(3)}&south=${view.south.toFixed(3)}&east=${view.east.toFixed(3)}&north=${view.north.toFixed(3)}` : "zoom=5";
  const { data, error, isFetching } = useDash<Geo>("geography", g.query, extra);
  const cities = useDash<Cities>("cities", g.query);
  if (error) return <ErrorBox error={error} />;
  const cityRows = (rows: CityRow[]) => rows.map((c) => ({
    name: c.name, region: c.admin1 ?? "–", population: c.population != null ? formatCompact(c.population) : "–",
    businesses: formatNumber(c.business_count), per10k: c.businesses_per_10k ?? "–",
    web: c.business_count ? pct(c.with_website, c.business_count) : "–",
  }));
  const cityColumns = [{ key: "name", label: "City" }, { key: "region", label: "Region" }, { key: "population", label: "Population", numeric: true },
    { key: "businesses", label: "Businesses", numeric: true }, { key: "per10k", label: "Per 10k people", numeric: true }, { key: "web", label: "With website", numeric: true }];
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="relative h-[520px] overflow-hidden lg:col-span-2">
        <DensityMap cells={data?.grid.cells ?? []} onView={setView} />
        <div className="absolute bottom-2 left-2 z-[500] rounded-md bg-surface/90 px-2 py-1 text-[11px] text-ink-2 shadow-card">
          {isFetching ? <Spinner size={11} /> : `${formatNumber(data?.grid.cells.length)} grid cells`} · server-side aggregation; zoom in for detail
        </div>
      </Card>
      <ChartCard title="By region" chart={<BarList items={(data?.by_region ?? []).slice(0, 12).map((r) => ({ key: r.label, label: r.label, value: r.count }))} onSelect={(v) => g.toggle("admin1", v)} selected={g.valuesFor("admin1")} />} />
      <ChartCard title="Top cities" subtitle="A city can span regions (e.g. Rawalpindi)" chart={<BarList
        items={(data?.by_city ?? []).slice(0, 15).map((r) => ({ key: `${r.label}|${r.parent}`, label: r.label, hint: r.parent, value: r.count }))}
        onSelect={(key) => g.toggle("city", key.split("|")[0])}
        selected={(data?.by_city ?? []).filter((r) => g.valuesFor("city").includes(r.label)).map((r) => `${r.label}|${r.parent}`)} />} />
      <ChartCard title="By country" chart={<BarList items={(data?.by_country ?? []).map((r) => ({ key: r.label, label: r.label, value: r.count }))} onSelect={(v) => g.toggle("country_code", v)} selected={g.valuesFor("country_code")} />} />
      <ChartCard className="lg:col-span-2" title="Largest cities: coverage per resident"
        subtitle={cities.data?.summary.gazetteer ? `Population from ${cities.data.summary.gazetteer}; businesses per 10,000 people` : "Run GeoNames for populations"}
        chart={cities.data?.largest.length ? <SimpleTable columns={cityColumns} rows={cityRows(cities.data.largest)} /> : <Empty>No population data yet</Empty>} />
      <ChartCard title="Least covered large cities" subtitle="100k+ people, fewest mapped businesses per resident"
        chart={cities.data?.underserved.length ? <SimpleTable columns={cityColumns.filter((c) => c.key !== "web" && c.key !== "region")} rows={cityRows(cities.data.underserved)} /> : <Empty>–</Empty>} />
    </div>
  );
}

interface CityRow { city_id: string; name: string; admin1: string | null; population: number | null; business_count: number; businesses_per_10k: number | null; with_website: number }
interface Cities { summary: Record<string, number | string | null>; largest: CityRow[]; underserved: CityRow[] }

// ---------------------------------------------------------------- categories

interface Cat { groups: { label: string; count: number }[]; categories: { label: string; parent: string; count: number }[]; by_source: { source: string; label: string; count: number }[]; by_city: { city: string; label: string; count: number }[] }

function Categories({ g }: { g: Global }) {
  const [group, setGroup] = useState<string | null>(null);
  const { data, error } = useDash<Cat>("categories", g.query, group ? `group=${encodeURIComponent(group)}` : "");
  if (error) return <ErrorBox error={error} />;
  const cities = [...new Set((data?.by_city ?? []).map((r) => r.city))];
  const groups = (data?.groups ?? []).map((r) => r.label);
  const max = Math.max(1, ...(data?.by_city ?? []).map((r) => r.count));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ChartCard title="Businesses by category group" subtitle="Click a group to drill into its categories"
        chart={<BarList items={(data?.groups ?? []).map((r) => ({ key: r.label, label: r.label, value: r.count }))} onSelect={(v) => setGroup(group === v ? null : v)} selected={group ? [group] : []} />} />
      <ChartCard title={group ? `Categories in ${group}` : "Top categories"} subtitle="Click to open the records"
        actions={group ? <button onClick={() => setGroup(null)} className="text-xs text-accent-ink hover:underline">All groups</button> : undefined}
        chart={
          <ul className="flex flex-col">
            {(data?.categories ?? []).slice(0, 15).map((c) => (
              <li key={`${c.parent}:${c.label}`}>
                <Link href={explorerHref("gold.businesses", g.filters, [{ field: "category", operator: "in", value: [c.label] }])}
                  className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 rounded-md px-1.5 py-1 hover:bg-surface-hover">
                  <span className="truncate text-sm">{humanize(c.label)} <span className="text-xs text-muted">{c.parent}</span></span>
                  <span className="tabular text-sm text-ink-2">{formatNumber(c.count)}</span>
                </Link>
              </li>
            ))}
          </ul>
        } />
      <ChartCard className="lg:col-span-2" title="Category groups by city" subtitle="Top 12 cities; darker = more businesses. Click a cell to open the records."
        chart={
          cities.length ? (
            <div className="overflow-x-auto">
              <table className="w-full border-separate border-spacing-0.5 text-xs">
                <thead><tr><th className="px-2 text-left font-medium text-muted">City</th>{groups.map((gr) => <th key={gr} className="px-1 text-right font-medium text-ink-2">{gr}</th>)}</tr></thead>
                <tbody>
                  {cities.map((city) => (
                    <tr key={city}>
                      <th className="whitespace-nowrap px-2 text-left font-medium">{city}</th>
                      {groups.map((gr) => {
                        const value = data!.by_city.find((r) => r.city === city && r.label === gr)?.count ?? 0;
                        const intensity = value / max;
                        return (
                          <td key={gr} className="p-0">
                            <Link href={explorerHref("gold.businesses", g.filters, [{ field: "city", operator: "in", value: [city] }, { field: "category_group", operator: "in", value: [gr] }])}
                              className="tabular block rounded px-1.5 py-1 text-right"
                              style={{ background: value ? `color-mix(in oklab, var(--series-1) ${Math.round(8 + intensity * 82)}%, var(--surface))` : "var(--surface-2)", color: intensity > 0.45 ? "#fff" : "var(--ink)" }}>
                              {value ? formatNumber(value) : "·"}
                            </Link>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty>No data</Empty>
        } />
    </div>
  );
}

// ---------------------------------------------------------------- domains

interface Crawl { summary: Record<string, number | string | null>; by_type: { label: string; domains: number; with_businesses: number }[];
  languages: { label: string; domains: number }[]; discovery_leads: { label: string; domain_type: string; crawl_pages: number; homepage_status: number | null; language: string | null }[] }
interface Dom { summary: Record<string, number>; tlds: { label: string; domains: number; businesses: number }[]; top_domains: { label: string; businesses: number; cities: number }[]; by_category: Row[]; by_region: Row[]; by_source: Row[]; crawl: Crawl }

const LANGUAGES: Record<string, string> = { eng: "English", urd: "Urdu", pus: "Pashto", snd: "Sindhi", pan: "Punjabi", ara: "Arabic", fas: "Persian", hin: "Hindi" };

function Domains({ g }: { g: Global }) {
  const { data, error } = useDash<Dom>("domains", g.query);
  if (error) return <ErrorBox error={error} />;
  const s = data?.summary ?? {};
  const c = (data?.crawl.summary ?? {}) as Record<string, number>;
  const coverage = (rows: Row[] | undefined, field: string) => (
    <BarList max={100} items={(rows ?? []).map((r) => ({ key: String(r.label), label: String(r.label), value: Number(r.domain_pct), display: `${r.domain_pct}%`, hint: formatNumber(r.businesses as number) }))}
      onSelect={(v) => g.toggle(field, v)} selected={g.valuesFor(field)} />
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Unique domains" value={formatNumber(s.unique_domains)} />
        <Drill g={g} extra={[{ field: "domain", operator: "is_not_null" }]}><StatTile label="Businesses with a domain" value={formatNumber(s.with_domain)} detail={pct(s.with_domain, s.businesses)} /></Drill>
        <Drill g={g} extra={[{ field: "domain", operator: "is_null" }]}><StatTile label="Without a domain" value={formatNumber(s.without_domain)} detail={pct(s.without_domain, s.businesses)} /></Drill>
        <Drill g={g} extra={[{ field: "domain", operator: "is_null" }, { field: "website", operator: "is_not_null" }]}><StatTile label="Social / platform page only" value={formatNumber(s.platform_only)} detail="e.g. facebook.com — enrichment targets" /></Drill>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="TLD distribution" subtitle="Unique domains per top-level domain"
          chart={<BarList items={(data?.tlds ?? []).map((t) => ({ key: t.label, label: `.${t.label}`, value: t.domains, hint: `${formatNumber(t.businesses)} businesses` }))} />} />
        <ChartCard title="Most used domains" subtitle="Domains shared by many locations are usually chains"
          chart={<BarList items={(data?.top_domains ?? []).slice(0, 15).map((t) => ({ key: t.label, label: t.label, value: t.businesses, hint: `${t.cities} cities` }))} />} />
        <ChartCard title="Domain coverage by category" chart={coverage(data?.by_category, "category_group")} />
        <ChartCard title="Domain coverage by region" chart={coverage(data?.by_region, "admin1")} />
        <ChartCard title="Domain coverage by source" chart={coverage(data?.by_source, "sources")} />
      </div>
      <h2 className="mt-2 text-sm font-semibold text-ink-2">Web crawl {data?.crawl.summary.crawl ? <span className="font-normal text-muted">· {String(data.crawl.summary.crawl)}</span> : null}</h2>
      {data?.crawl.summary.crawled_domains ? (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile label="Business domains in the crawl" value={formatNumber(c.business_domains_crawled)} detail={pct(c.business_domains_crawled, c.business_domains)} />
            <Link href={explorerHref("gold.domains", NONE, [{ field: "business_count", operator: "equals", value: 0 }, { field: "crawl_pages", operator: "is_not_null" }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
              <StatTile label="Discovered domains" value={formatNumber(c.discovered_only)} detail="crawled, not yet linked to a business" />
            </Link>
            <Link href={explorerHref("gold.domains", NONE, [{ field: "business_count", operator: "equals", value: 0 }, { field: "is_business_candidate", operator: "equals", value: true }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
              <StatTile label="Business candidates" value={formatNumber(c.discovered_candidates)} detail="commercial, live, not spam" />
            </Link>
            <StatTile label="Live homepages" value={formatNumber(c.live_homepages)} detail={pct(c.live_homepages, c.crawled_domains)} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <ChartCard title="Crawled domains by type" subtitle="From the second-level label (com.pk, edu.pk, gov.pk…)"
              chart={<BarList items={(data.crawl.by_type ?? []).map((t) => ({ key: t.label, label: humanize(t.label), value: t.domains, hint: `${formatNumber(t.with_businesses)} linked` }))} />} />
            <ChartCard title="Main language of crawled sites" chart={<BarList items={(data.crawl.languages ?? []).map((l) => ({ key: l.label, label: LANGUAGES[l.label] ?? l.label, value: l.domains }))} />} />
            <ChartCard title="Discovery leads" subtitle="Largest business-like sites no business links to yet"
              chart={
                <ul className="flex flex-col">
                  {data.crawl.discovery_leads.slice(0, 12).map((d) => (
                    <li key={d.label} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 px-1.5 py-1">
                      <a href={`https://${d.label}`} target="_blank" rel="noreferrer" className="truncate text-sm hover:underline">{d.label}</a>
                      <span className="tabular text-sm text-ink-2">{formatNumber(d.crawl_pages)} <span className="text-xs text-muted">pages</span></span>
                    </li>
                  ))}
                </ul>
              } />
          </div>
        </>
      ) : <Card className="p-4 text-sm text-ink-2">Run the Common Crawl source to see which business domains are crawled and discover new ones.</Card>}
    </div>
  );
}

// ---------------------------------------------------------------- quality

interface QualityData { per_source: Row[]; rejects: { source: string; reason: string; count: number }[]; matches: { status: string; count: number }[]; conflicts: { conflict: string; count: number }[];
  checks: Row[]; check_pass_rate: number | null; gold: { avg_completeness: number; businesses: number; conflicted: number }; ignored_filters: string[] }

const ISSUES: { key: string; label: string; conditions: Condition[] }[] = [
  { key: "missing_names", label: "Missing names", conditions: [{ field: "has_name", operator: "equals", value: false }] },
  { key: "missing_categories", label: "Missing categories", conditions: [{ field: "category", operator: "is_null" }] },
  { key: "missing_addresses", label: "Missing addresses", conditions: [{ field: "address", operator: "is_null" }] },
  { key: "missing_websites", label: "Missing websites", conditions: [{ field: "website", operator: "is_null" }] },
  { key: "unassigned_country", label: "Outside known boundaries", conditions: [{ field: "country_code", operator: "is_null" }] },
  { key: "invalid_domains", label: "Invalid websites/domains", conditions: [{ field: "normalized_domain", operator: "is_null" }, { field: "attributes", operator: "contains", value: "\"website\"" }] },
  { key: "invalid_phones", label: "Invalid phones", conditions: [{ field: "phone", operator: "is_not_null" }, { field: "normalized_phone", operator: "is_null" }] },
];

function Quality({ g }: { g: Global }) {
  const { data, error } = useDash<QualityData>("quality", g.query);
  if (error) return <ErrorBox error={error} />;
  const sources = data?.per_source ?? [];
  const totals = (key: string) => sources.reduce((sum, r) => sum + Number(r[key] ?? 0), 0);
  const records = totals("records");
  const silverFilters: FilterGroup = { operator: "and", conditions: g.filters.conditions.filter((c) => !isGroup(c) && ["country_code", "admin1", "city", "category_group", "category"].includes(c.field)) };
  return (
    <div className="flex flex-col gap-4">
      {data?.ignored_filters.length ? <p className="text-xs text-muted">Silver metrics ignore filters that only exist in Gold: {data.ignored_filters.join(", ")}</p> : null}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
        {ISSUES.map((issue) => (
          <Link key={issue.key} href={explorerHref("silver.places", silverFilters, issue.conditions)} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
            <StatTile label={issue.label} value={formatNumber(totals(issue.key))} detail={pct(totals(issue.key), records)} />
          </Link>
        ))}
        <Link href={explorerHref("er.entity_matches", { operator: "and", conditions: [] }, [{ field: "status", operator: "in", value: ["suggested"] }])} className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="Duplicate candidates" value={formatNumber(data?.matches.find((m) => m.status === "suggested")?.count ?? 0)} detail="suggested matches to review" />
        </Link>
        <Drill g={g} extra={[{ field: "conflicts", operator: "in", value: ["phone", "domain", "category", "name"] }]}><StatTile label="Conflicting records" value={formatNumber(data?.gold.conflicted)} detail="merged records disagree on a field" /></Drill>
        <Link href="/data-platform/explorer?dataset=silver.rejects" className="block rounded-xl hover:ring-2 hover:ring-accent/40">
          <StatTile label="Rejected records" value={formatNumber(data?.rejects.reduce((s, r) => s + r.count, 0))} detail="failed normalization" />
        </Link>
        <StatTile label="Quality checks passing" value={data?.check_pass_rate != null ? `${data.check_pass_rate}%` : "–"} detail={`${data?.checks.length ?? 0} latest checks`} />
        <StatTile label="Gold completeness" value={data?.gold.avg_completeness != null ? `${data.gold.avg_completeness}%` : "–"} detail="avg over businesses" />
      </div>
      <ChartCard title="Coverage by source" subtitle="Share of Silver records with each field — the comparison that shows a source's real value"
        chart={<SimpleTable columns={[{ key: "source", label: "Source" }, { key: "records", label: "Records", numeric: true }, { key: "name", label: "Name", numeric: true },
          { key: "web", label: "Website", numeric: true }, { key: "phone", label: "Phone", numeric: true }, { key: "address", label: "Address", numeric: true }]}
          rows={sources.map((r) => ({ source: r.source, records: formatNumber(r.records as number), name: `${r.name_pct}%`, web: `${r.website_pct}%`, phone: `${r.phone_pct}%`, address: `${r.address_pct}%` }))} />} />
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Conflicts in merged businesses" chart={<BarList items={(data?.conflicts ?? []).map((c) => ({ key: c.conflict, label: humanize(c.conflict), value: c.count }))} />} />
        <ChartCard title="Entity matches by status" chart={<BarList items={(data?.matches ?? []).map((m) => ({ key: m.status, label: humanize(m.status), value: m.count }))} />} />
      </div>
      <ChartCard title="Latest data quality checks" subtitle="Most recent result of every check per dataset"
        chart={<SimpleTable columns={[{ key: "dataset", label: "Dataset" }, { key: "check", label: "Check" }, { key: "severity", label: "Severity" }, { key: "status", label: "Status" },
          { key: "failed", label: "Failed", numeric: true }, { key: "pct", label: "Failure %", numeric: true }, { key: "threshold", label: "Threshold %", numeric: true }]}
          rows={(data?.checks ?? []).map((c) => ({ dataset: c.dataset_id, check: c.check_name, severity: c.severity, status: humanize(c.status as string), failed: formatNumber(c.records_failed as number), pct: c.failure_percentage ?? "–", threshold: c.threshold_percentage }))} />} />
    </div>
  );
}

// ---------------------------------------------------------------- operations

interface Ops { runs: number; succeeded: number; failed: number; cancelled: number; active: number; avg_duration_seconds: number | null; bytes_downloaded: number; records: number; failure_rate: number | null;
  stages: Row[]; over_time: { day: string; status: string; runs: number; bronze: number; silver: number; gold: number }[];
  throughput: { run_id: number; stage: string; records_per_sec: number }[]; storage_growth: { day: string; cumulative_bytes: number }[]; freshness: Row[] }

const STATUS_SERIES = [
  { key: "COMPLETED", label: "Completed", color: "var(--good)" },
  { key: "PARTIALLY_COMPLETED", label: "Partial", color: "var(--warning)" },
  { key: "FAILED", label: "Failed", color: "var(--critical)" },
  { key: "CANCELLED", label: "Cancelled", color: "var(--muted)" },
];

const axis = { tickLine: false, tick: { fill: "var(--muted)", fontSize: 11 } };
const tooltip = { contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }, labelStyle: { color: "var(--ink)" } };

function Operations() {
  const { data, error } = useQuery({ queryKey: ["dp-dash-ops"], queryFn: () => dp<Ops>("/dashboard/pipelines?days=90"), refetchInterval: 15_000 });
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const days = [...new Set(data.over_time.map((r) => r.day))];
  const runsByDay = days.map((day) => Object.fromEntries([["day", day], ...STATUS_SERIES.map((s) => [s.key, data.over_time.filter((r) => r.day === day && r.status === s.key).reduce((a, r) => a + r.runs, 0)])]));
  const recordsByDay = days.map((day) => ({ day, records: data.over_time.filter((r) => r.day === day).reduce((a, r) => a + r.bronze + r.silver + r.gold, 0) }));
  const throughput = [...new Set(data.throughput.map((t) => t.run_id))].map((id) => ({
    run: `#${id}`, ...Object.fromEntries(data.throughput.filter((t) => t.run_id === id).map((t) => [t.stage, Math.round(t.records_per_sec)])),
  }));
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <StatTile label="Runs (90 d)" value={formatNumber(data.runs)} detail={`${data.active} active`} />
        <StatTile label="Successful" value={formatNumber(data.succeeded)} />
        <StatTile label="Failed" value={formatNumber(data.failed)} />
        <StatTile label="Cancelled" value={formatNumber(data.cancelled)} />
        <StatTile label="Failure rate" value={data.failure_rate != null ? `${data.failure_rate}%` : "–"} />
        <StatTile label="Avg. duration" value={formatSeconds(data.avg_duration_seconds)} detail="completed runs" />
        <StatTile label="Downloaded" value={formatBytes(data.bytes_downloaded)} />
        <StatTile label="Records processed" value={formatCompact(data.records)} detail="Bronze + Silver + Gold" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Runs over time" subtitle="By outcome"
          chart={<div className="h-56"><ResponsiveContainer><BarChart data={runsByDay}><CartesianGrid vertical={false} stroke="var(--grid)" /><XAxis dataKey="day" {...axis} /><YAxis {...axis} allowDecimals={false} width={30} /><Tooltip {...tooltip} /><Legend wrapperStyle={{ fontSize: 12 }} formatter={(value) => <span style={{ color: "var(--ink-2)" }}>{value}</span>} />
            {STATUS_SERIES.map((s) => <Bar key={s.key} dataKey={s.key} name={s.label} stackId="a" fill={s.color} maxBarSize={36} />)}</BarChart></ResponsiveContainer></div>} />
        <ChartCard title="Records ingested over time" subtitle="Bronze + Silver + Gold rows written per day"
          chart={<div className="h-56"><ResponsiveContainer><BarChart data={recordsByDay}><CartesianGrid vertical={false} stroke="var(--grid)" /><XAxis dataKey="day" {...axis} /><YAxis {...axis} tickFormatter={(v) => formatCompact(v)} width={44} /><Tooltip {...tooltip} formatter={(v) => formatNumber(Number(v))} />
            <Bar dataKey="records" name="Records" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={36} /></BarChart></ResponsiveContainer></div>} />
        <ChartCard title="Processing throughput" subtitle="Records per second by stage, per run"
          chart={throughput.length ? <div className="h-56"><ResponsiveContainer><LineChart data={throughput}><CartesianGrid vertical={false} stroke="var(--grid)" /><XAxis dataKey="run" {...axis} /><YAxis {...axis} tickFormatter={(v) => formatCompact(v)} width={44} /><Tooltip {...tooltip} formatter={(v) => `${formatNumber(Number(v))}/s`} /><Legend wrapperStyle={{ fontSize: 12 }} formatter={(value) => <span style={{ color: "var(--ink-2)" }}>{value}</span>} />
            {["BRONZE_INGEST", "SILVER_TRANSFORM", "GOLD_BUILD"].map((stage, i) => <Line key={stage} dataKey={stage} name={STAGE_LABELS[stage]} stroke={`var(--series-${i + 1})`} strokeWidth={2} dot={{ r: 3 }} />)}</LineChart></ResponsiveContainer></div> : <Empty>No completed stages yet</Empty>} />
        <ChartCard title="Storage growth" subtitle="Artifacts + committed dataset versions"
          chart={<div className="h-56"><ResponsiveContainer><AreaChart data={data.storage_growth}><CartesianGrid vertical={false} stroke="var(--grid)" /><XAxis dataKey="day" {...axis} /><YAxis {...axis} tickFormatter={(v) => formatBytes(v)} width={60} /><Tooltip {...tooltip} formatter={(v) => formatBytes(Number(v))} />
            <Area dataKey="cumulative_bytes" name="Stored" stroke="var(--series-1)" fill="var(--series-1)" fillOpacity={0.15} strokeWidth={2} /></AreaChart></ResponsiveContainer></div>} />
      </div>
      <ChartCard title="Stages" subtitle="Executions, idempotent skips, failures and retries per stage"
        chart={<SimpleTable columns={[{ key: "stage", label: "Stage" }, { key: "executed", label: "Executed", numeric: true }, { key: "skipped", label: "Skipped", numeric: true },
          { key: "failed", label: "Failed", numeric: true }, { key: "retries", label: "Retries", numeric: true }, { key: "secs", label: "Avg. time", numeric: true }, { key: "rate", label: "Avg. records/s", numeric: true }]}
          rows={data.stages.map((s) => ({ stage: STAGE_LABELS[s.stage as string] ?? s.stage, executed: s.executed, skipped: s.skipped, failed: s.failed, retries: s.retries,
            secs: formatSeconds(s.avg_seconds as number), rate: formatNumber(s.avg_records_per_sec as number) }))} />} />
      <ChartCard title="Freshness" chart={<SimpleTable columns={[{ key: "pipeline", label: "Pipeline" }, { key: "success", label: "Last successful run" }, { key: "data", label: "Latest data release" }]}
        rows={data.freshness.map((f) => ({ pipeline: f.pipeline, success: `${formatDateTime(f.last_success as string)} (${timeAgo(f.last_success as string)})`, data: formatDateTime(f.latest_data as string) }))} />} />
    </div>
  );
}
