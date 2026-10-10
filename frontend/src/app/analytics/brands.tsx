"use client";

import { BarList } from "@/components/charts/bar-list";
import { ChartCard } from "@/components/charts/chart-card";
import { Card, ErrorBox, Loading, Meter } from "@/components/ui";
import { useFilters } from "@/lib/filters";
import { formatNumber, humanize } from "@/lib/format";
import { useApi, withQuery } from "@/lib/hooks";
import type { BrandRow } from "@/lib/types";

export function BrandsTab() {
  const { filters, queryString, setFilters } = useFilters();
  const { data, error, isLoading, isFetching } = useApi<BrandRow[]>(withQuery("/api/stats/brands", queryString, { limit: 200 }));

  const toggleBrand = (brand: string) =>
    setFilters({ brand: filters.brand.includes(brand) ? filters.brand.filter((b) => b !== brand) : [...filters.brand, brand] });

  if (error) return <ErrorBox error={error} />;
  if (isLoading) return <Loading />;

  return (
    <div className="grid gap-4 xl:grid-cols-[380px_1fr]">
      <ChartCard
        title="Largest chains"
        subtitle="Locations per brand — click to filter"
        loading={isFetching}
        chart={
          <BarList
            items={(data ?? []).slice(0, 20).map((b) => ({ key: b.brand, label: b.brand, hint: humanize(b.category), value: b.locations }))}
            onSelect={toggleBrand}
            selected={filters.brand}
          />
        }
      />
      <Card className="overflow-hidden">
        <div className="border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">Chain footprint</h2>
          <p className="text-xs text-muted">{formatNumber(data?.length ?? 0)} brands (by brand / brand:wikidata tag)</p>
        </div>
        <div className="max-h-[640px] overflow-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="sticky top-0 bg-surface-2 text-left text-xs text-ink-2">
              <tr>
                <th className="px-3 py-2 font-medium">Brand</th>
                <th className="px-3 py-2 font-medium">Category</th>
                <th className="px-3 py-2 text-right font-medium">Locations</th>
                <th className="px-3 py-2 text-right font-medium">Cities</th>
                <th className="px-3 py-2 text-right font-medium">Provinces</th>
                <th className="px-3 py-2 font-medium">Main city</th>
                <th className="px-3 py-2 font-medium">With phone</th>
              </tr>
            </thead>
            <tbody>
              {data?.map((b) => (
                <tr key={b.brand} onClick={() => toggleBrand(b.brand)} className="cursor-pointer border-b border-line/60 hover:bg-surface-hover">
                  <td className="px-3 py-1.5 font-medium">{b.brand}</td>
                  <td className="px-3 py-1.5 text-ink-2">
                    {humanize(b.category)} <span className="text-xs text-muted">· {b.category_group}</span>
                  </td>
                  <td className="tabular px-3 py-1.5 text-right">{formatNumber(b.locations)}</td>
                  <td className="tabular px-3 py-1.5 text-right">{formatNumber(b.cities)}</td>
                  <td className="tabular px-3 py-1.5 text-right">{b.provinces}</td>
                  <td className="px-3 py-1.5 text-ink-2">{b.top_city ?? "–"}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-2">
                      <Meter value={b.pct_phone} className="w-16" />
                      <span className="tabular text-xs text-ink-2">{b.pct_phone}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
