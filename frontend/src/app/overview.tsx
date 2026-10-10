"use client";

import { BarList } from "@/components/charts/bar-list";
import { ChartCard, SimpleTable } from "@/components/charts/chart-card";
import { ColumnChart } from "@/components/charts/column-chart";
import { FilterBar } from "@/components/filter-bar";
import { StatTile } from "@/components/stat-tile";
import { ErrorBox, PageHeader } from "@/components/ui";
import { FLAG_LABELS, useFilters, type FlagKey, type ListKey } from "@/lib/filters";
import { formatDate, formatNumber, formatPercent, humanize } from "@/lib/format";
import { useApi, withQuery } from "@/lib/hooks";
import type { Count, Overview } from "@/lib/types";

const COVERAGE: { key: FlagKey; field: keyof Overview["kpis"] }[] = [
  { key: "has_address", field: "with_address" },
  { key: "has_phone", field: "with_phone" },
  { key: "has_opening_hours", field: "with_opening_hours" },
  { key: "has_website", field: "with_website" },
  { key: "has_email", field: "with_email" },
];

export function OverviewPage() {
  const { filters, queryString, setFilters } = useFilters();
  const { data, error, isFetching } = useApi<Overview>(withQuery("/api/stats/overview", queryString));

  const toggle = (key: ListKey) => (value: string) =>
    setFilters({ [key]: filters[key].includes(value) ? filters[key].filter((v) => v !== value) : [...filters[key], value] });

  const k = data?.kpis;
  const total = k?.total ?? 0;

  const countTable = (rows: Count[], label: string) => (
    <SimpleTable
      columns={[
        { key: "label", label },
        { key: "count", label: "Companies", numeric: true },
        { key: "share", label: "Share", numeric: true },
      ]}
      rows={rows.map((r) => ({ label: r.label, count: formatNumber(r.count), share: formatPercent(r.count, total) }))}
    />
  );

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader
        title="Overview"
        description="Named businesses mapped in OpenStreetMap — click any bar to filter everything on the page."
      />
      <FilterBar />
      {error && <ErrorBox error={error} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Companies" value={formatNumber(k?.total)} detail={k ? `${formatNumber(k.categories)} categories` : " "} />
        <StatTile label="With phone" value={formatPercent(k?.with_phone ?? 0, total)} detail={`${formatNumber(k?.with_phone)} companies`} />
        <StatTile label="With website" value={formatPercent(k?.with_website ?? 0, total)} detail={`${formatNumber(k?.with_website)} companies`} />
        <StatTile label="Avg. completeness" value={k?.avg_completeness != null ? `${k.avg_completeness}%` : "–"} detail="of 5 contact fields" />
        <StatTile label="Chains" value={formatNumber(k?.brands)} detail={`${formatNumber(k?.branded)} branded outlets`} />
        <StatTile label="Cities" value={formatNumber(k?.cities)} detail={`Latest edit ${formatDate(k?.latest_edit)}`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Companies by category group"
          loading={isFetching}
          chart={
            <BarList
              items={(data?.by_group ?? []).map((r) => ({ key: String(r.label), label: r.label, value: r.count }))}
              onSelect={toggle("group")}
              selected={filters.group}
              total={total}
            />
          }
          table={data && countTable(data.by_group, "Group")}
        />
        <ChartCard
          title="Top categories"
          subtitle="The OSM tag value that classifies each company"
          loading={isFetching}
          chart={
            <BarList
              items={(data?.top_categories ?? []).map((r) => ({
                key: String(r.label),
                label: humanize(r.label),
                hint: r.group,
                value: r.count,
              }))}
              onSelect={toggle("category")}
              selected={filters.category}
              total={total}
            />
          }
          table={data && countTable(data.top_categories.map((r) => ({ ...r, label: humanize(r.label) })), "Category")}
        />
        <ChartCard
          title="Top cities"
          loading={isFetching}
          chart={
            <BarList
              items={(data?.top_cities ?? []).map((r) => ({ key: String(r.label), label: r.label, value: r.count }))}
              onSelect={toggle("city")}
              selected={filters.city}
              total={total}
            />
          }
          table={data && countTable(data.top_cities, "City")}
        />
        <ChartCard
          title="Companies by province"
          loading={isFetching}
          chart={
            <BarList
              items={(data?.by_province ?? []).map((r) => ({ key: String(r.label), label: r.label, value: r.count }))}
              onSelect={toggle("province")}
              selected={filters.province}
              total={total}
            />
          }
          table={data && countTable(data.by_province, "Province")}
        />
        <ChartCard
          title="Contact data coverage"
          subtitle="Share of companies that have each field — click to keep only those"
          loading={isFetching}
          chart={
            <BarList
              max={100}
              items={COVERAGE.map(({ key, field }) => {
                const value = total ? ((k?.[field] as number) / total) * 100 : 0;
                return { key, label: FLAG_LABELS[key], value, display: `${value.toFixed(1)}%`, hint: formatNumber(k?.[field] as number) };
              })}
              selected={COVERAGE.filter(({ key }) => filters[key]).map(({ key }) => key)}
              onSelect={(key) => setFilters({ [key]: filters[key as FlagKey] ? undefined : true })}
            />
          }
        />
        <ChartCard
          title="Profile completeness"
          subtitle="Companies by % of contact fields filled (phone, website, email, address, hours)"
          loading={isFetching}
          chart={
            <ColumnChart
              data={(data?.completeness ?? []).map((r) => ({ label: `${r.label}%`, value: r.count }))}
              onSelect={(label) => setFilters({ min_completeness: parseInt(label) || undefined })}
            />
          }
          table={data && countTable(data.completeness.map((r) => ({ ...r, label: `${r.label}%` })), "Completeness")}
        />
        <ChartCard
          className="lg:col-span-2"
          title="Data freshness"
          subtitle="Companies by the year their OSM record was last edited"
          loading={isFetching}
          chart={<ColumnChart data={(data?.edits_by_year ?? []).map((r) => ({ label: String(r.label), value: r.count }))} />}
          table={data && countTable(data.edits_by_year, "Year")}
        />
      </div>
    </div>
  );
}
