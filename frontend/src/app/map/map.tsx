"use client";

import { Info, Maximize2 } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { CompanyDrawer } from "@/components/company-drawer";
import { FilterBar } from "@/components/filter-bar";
import { CompaniesMap, type Bounds } from "@/components/map";
import { Button, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { useFilters } from "@/lib/filters";
import { formatNumber } from "@/lib/format";
import { useApi, useDebounced, withQuery } from "@/lib/hooks";
import type { CompanyRef, MapPoint } from "@/lib/types";

const LIMIT = 15_000;
const NO_POINTS: MapPoint[] = [];

function roundBounds(bounds: Bounds) {
  // Round so tiny pans reuse the cached request.
  const r = (v: number) => Math.round(v * 100) / 100;
  return { west: r(bounds.west) - 0.01, south: r(bounds.south) - 0.01, east: r(bounds.east) + 0.01, north: r(bounds.north) + 0.01 };
}

export function MapPage() {
  const { queryString } = useFilters();
  const [bounds, setBounds] = useState<Bounds | null>(null);
  const [selected, setSelected] = useState<CompanyRef | null>(null);
  const debouncedBounds = useDebounced(bounds, 250);

  const onBoundsChange = useCallback((next: Bounds) => setBounds(roundBounds(next)), []);

  // Zoom to the filtered companies whenever the filters change, or on demand.
  const extent = useApi<Bounds & { count: number }>(withQuery("/api/map/extent", queryString));
  const autoFit = useMemo(
    () => (queryString && extent.data?.count && !extent.isPlaceholderData ? { ...extent.data } : null),
    [queryString, extent.data, extent.isPlaceholderData],
  );
  const [manualFit, setManualFit] = useState<Bounds | null>(null);
  const fitToResults = () => extent.data?.count && setManualFit({ ...extent.data });

  const { data, error, isFetching } = useApi<{ truncated: boolean; points: MapPoint[] }>(
    debouncedBounds ? withQuery("/api/map/points", queryString, { ...debouncedBounds, limit: LIMIT }) : null,
  );

  return (
    <div className="flex h-[calc(100dvh-53px)] flex-col gap-3 p-4 lg:h-dvh lg:p-6">
      <PageHeader
        title="Map"
        description="Companies in view. Pan and zoom to load more; click a point for details."
        actions={
          <div className="flex items-center gap-2 text-sm text-ink-2">
            {isFetching && <Spinner size={14} />}
            <Button size="sm" variant="ghost" onClick={fitToResults} title="Zoom to all companies matching the filters">
              <Maximize2 size={13} /> Fit to results
            </Button>
            {data && (
              <span className="tabular">
                {formatNumber(data.points.length)} {data.truncated ? `shown (first ${formatNumber(LIMIT)})` : "in view"}
              </span>
            )}
          </div>
        }
      />
      <FilterBar />
      {error && <ErrorBox error={error} />}
      {data?.truncated && (
        <div className="flex items-center gap-2 text-xs text-ink-2">
          <Info size={14} className="text-accent" />
          More companies are in view than can be drawn at once — showing the most complete profiles. Zoom in or filter to see all.
        </div>
      )}
      <div className="relative min-h-80 flex-1 overflow-hidden rounded-xl border border-line">
        <CompaniesMap points={data?.points ?? NO_POINTS} fitTo={[autoFit, manualFit]} onBoundsChange={onBoundsChange} onOpen={setSelected} />
      </div>
      <CompanyDrawer selected={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
