"use client";

import clsx from "clsx";
import { useState } from "react";
import { CompanyDrawer } from "@/components/company-drawer";
import { Card, Empty, ErrorBox, Loading, Select, Spinner } from "@/components/ui";
import { FLAG_KEYS, FLAG_LABELS, useFilters, useUrlParam } from "@/lib/filters";
import { formatNumber, humanize } from "@/lib/format";
import { useApi, withQuery } from "@/lib/hooks";
import { DIMENSIONS, dimensionLabel, type CompanyRef, type DuplicatePair, type QualityRow } from "@/lib/types";

export function QualityTab() {
  const { queryString } = useFilters();
  const [by, setBy] = useUrlParam("by", "category_group");
  const [distance, setDistance] = useState(100);
  const [selected, setSelected] = useState<CompanyRef | null>(null);

  const quality = useApi<QualityRow[]>(withQuery("/api/stats/quality", queryString, { by, limit: 40 }));
  const dupes = useApi<DuplicatePair[]>(withQuery("/api/stats/duplicates", queryString, { distance_m: distance, limit: 300 }));

  return (
    <div className="flex flex-col gap-4">
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              Contact data coverage by {dimensionLabel(by).toLowerCase()} {quality.isFetching && <Spinner size={13} />}
            </h2>
            <p className="text-xs text-muted">Share of companies with each field filled. Find where enrichment is needed most.</p>
          </div>
          <Select label="Break down by" value={by} onChange={setBy} options={DIMENSIONS} />
        </div>
        {quality.error && <ErrorBox error={quality.error} className="m-4" />}
        {quality.isLoading && <Loading />}
        {quality.data && (
          <div className={clsx("overflow-auto transition-opacity", quality.isFetching && "opacity-60")}>
            <table className="w-full min-w-[860px] text-sm">
              <thead className="bg-surface-2 text-left text-xs text-ink-2">
                <tr>
                  <th className="px-3 py-2 font-medium">{dimensionLabel(by)}</th>
                  <th className="px-3 py-2 text-right font-medium">Companies</th>
                  {FLAG_KEYS.map((k) => (
                    <th key={k} className="px-3 py-2 font-medium">
                      {FLAG_LABELS[k]}
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right font-medium">Avg. score</th>
                </tr>
              </thead>
              <tbody>
                {quality.data.map((row) => (
                  <tr key={row.label} className="border-b border-line/60 last:border-0">
                    <td className="px-3 py-1.5 font-medium">{by === "category" ? humanize(row.label) : row.label}</td>
                    <td className="tabular px-3 py-1.5 text-right">{formatNumber(row.count)}</td>
                    {FLAG_KEYS.map((k) => (
                      <td key={k} className="px-3 py-1.5">
                        <CoverageCell value={row[k]} />
                      </td>
                    ))}
                    <td className="tabular px-3 py-1.5 text-right">{row.avg_completeness}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              Possible duplicates {dupes.isFetching && <Spinner size={13} />}
            </h2>
            <p className="text-xs text-muted">Companies with the same name mapped close together — likely double-mapped.</p>
          </div>
          <Select
            label="Within"
            value={String(distance)}
            onChange={(v) => setDistance(Number(v))}
            options={[10, 50, 100, 250, 500, 1000].map((d) => ({ value: String(d), label: `${d} m` }))}
          />
        </div>
        {dupes.error && <ErrorBox error={dupes.error} className="m-4" />}
        {dupes.isLoading && <Loading />}
        {dupes.data && dupes.data.length === 0 && <Empty>No likely duplicates found</Empty>}
        {dupes.data && dupes.data.length > 0 && (
          <div className="max-h-[480px] overflow-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="sticky top-0 bg-surface-2 text-left text-xs text-ink-2">
                <tr>
                  <th className="px-3 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">City</th>
                  <th className="px-3 py-2 font-medium">First record</th>
                  <th className="px-3 py-2 font-medium">Second record</th>
                  <th className="px-3 py-2 text-right font-medium">Distance</th>
                </tr>
              </thead>
              <tbody>
                {dupes.data.map((d) => (
                  <tr key={`${d.a_type}${d.a_id}-${d.b_type}${d.b_id}`} className="border-b border-line/60 last:border-0">
                    <td className="px-3 py-1.5 font-medium">{d.name}</td>
                    <td className="px-3 py-1.5 text-ink-2">{d.city ?? "–"}</td>
                    <td className="px-3 py-1.5">
                      <RecordLink type={d.a_type} id={d.a_id} category={d.a_category} onOpen={setSelected} />
                    </td>
                    <td className="px-3 py-1.5">
                      <RecordLink type={d.b_type} id={d.b_id} category={d.b_category} onOpen={setSelected} />
                    </td>
                    <td className="tabular px-3 py-1.5 text-right">{formatNumber(d.distance_m)} m</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="px-4 py-2 text-xs text-muted">{formatNumber(dupes.data.length)} pairs (max 300 shown)</p>
          </div>
        )}
      </Card>

      <CompanyDrawer selected={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function CoverageCell({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent" style={{ width: `${value}%` }} />
      </div>
      <span className="tabular w-11 text-xs text-ink-2">{value}%</span>
    </div>
  );
}

function RecordLink({
  type,
  id,
  category,
  onOpen,
}: {
  type: CompanyRef["osm_type"];
  id: number;
  category: string;
  onOpen: (ref: CompanyRef) => void;
}) {
  return (
    <button onClick={() => onOpen({ osm_type: type, osm_id: id })} className="text-left hover:underline">
      <span className="font-mono text-xs text-accent-ink">
        {type}/{id}
      </span>
      <span className="ml-1.5 text-xs text-muted">{humanize(category)}</span>
    </button>
  );
}
