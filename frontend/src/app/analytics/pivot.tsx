"use client";

import clsx from "clsx";
import { ArrowLeftRight } from "lucide-react";
import { useState } from "react";
import { Card, Empty, ErrorBox, Loading, Segmented, Select, Spinner } from "@/components/ui";
import { useFilters, useUrlParam, type ListKey } from "@/lib/filters";
import { formatNumber, humanize } from "@/lib/format";
import { useApi, withQuery } from "@/lib/hooks";
import { DIMENSIONS, dimensionLabel, type Pivot } from "@/lib/types";

// Dimension -> filter key, for click-to-filter on headers.
const FILTER_KEY: Record<string, ListKey | undefined> = {
  category_group: "group",
  category: "category",
  province: "province",
  district: "district",
  city: "city",
  brand: "brand",
};

type Mode = "count" | "row" | "col";

export function PivotTab() {
  const { filters, queryString, setFilters } = useFilters();
  const [rows, setRows] = useUrlParam("rows", "city");
  const [cols, setCols] = useUrlParam("cols", "category_group");
  const [rowLimit, setRowLimit] = useState(20);
  const [mode, setMode] = useState<Mode>("count");

  const { data, error, isLoading, isFetching } = useApi<Pivot>(
    rows !== cols ? withQuery("/api/stats/pivot", queryString, { rows, cols, row_limit: rowLimit, col_limit: 12 }) : null,
  );

  const max = data ? Math.max(1, ...data.matrix.flat()) : 1;

  const share = (value: number, r: number, c: number) => {
    if (!data) return 0;
    if (mode === "row") return data.row_totals[r] ? value / data.row_totals[r] : 0;
    if (mode === "col") return data.col_totals[c] ? value / data.col_totals[c] : 0;
    return value / max;
  };

  const cellText = (value: number, r: number, c: number) =>
    mode === "count" ? formatNumber(value) : `${(share(value, r, c) * 100).toFixed(1)}%`;

  const label = (dimension: string, value: string) => (dimension === "category" ? humanize(value) : value);

  const filterBy = (dimension: string, value: string) => {
    const key = FILTER_KEY[dimension];
    if (key && !filters[key].includes(value)) setFilters({ [key]: [...filters[key], value] });
  };

  const dimensionOptions = DIMENSIONS.map((d) => ({ value: d.value, label: d.label }));

  return (
    <Card className="flex flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        <Select label="Rows" value={rows} onChange={setRows} options={dimensionOptions} />
        <button
          aria-label="Swap rows and columns"
          title="Swap"
          onClick={() => {
            setRows(cols);
            setCols(rows);
          }}
          className="rounded-md p-1.5 text-ink-2 hover:bg-surface-hover"
        >
          <ArrowLeftRight size={14} />
        </button>
        <Select label="Columns" value={cols} onChange={setCols} options={dimensionOptions} />
        <Select
          label="Top"
          value={String(rowLimit)}
          onChange={(v) => setRowLimit(Number(v))}
          options={[10, 20, 30, 50].map((n) => ({ value: String(n), label: `${n} rows` }))}
        />
        <div className="ml-auto flex items-center gap-2">
          {isFetching && <Spinner size={14} />}
          <Segmented
            size="sm"
            value={mode}
            onChange={setMode}
            options={[
              { value: "count", label: "Count" },
              { value: "row", label: "% of row" },
              { value: "col", label: "% of column" },
            ]}
          />
        </div>
      </div>

      {rows === cols && <Empty>Pick two different dimensions</Empty>}
      {error && <ErrorBox error={error} className="m-4" />}
      {isLoading && <Loading />}
      {data && data.rows.length === 0 && <Empty>No data for these filters</Empty>}

      {data && data.rows.length > 0 && (
        <div className={clsx("overflow-auto transition-opacity", isFetching && "opacity-60")}>
          <table className="w-full border-separate border-spacing-0.5 p-3 text-xs">
            <thead>
              <tr>
                <th className="sticky left-0 z-10 bg-surface px-2 py-1.5 text-left font-medium text-muted">
                  {dimensionLabel(rows)} ╲ {dimensionLabel(cols)}
                </th>
                {data.cols.map((c) => (
                  <th key={c} className="min-w-20 px-1.5 py-1.5 text-right align-bottom font-medium text-ink-2">
                    <button onClick={() => filterBy(cols, c)} className="hover:text-accent-ink hover:underline" title="Filter to this">
                      {label(cols, c)}
                    </button>
                  </th>
                ))}
                <th className="px-2 py-1.5 text-right font-semibold text-ink">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r, ri) => (
                <tr key={r}>
                  <th className="sticky left-0 z-10 max-w-56 truncate bg-surface px-2 py-1 text-left font-medium text-ink">
                    <button onClick={() => filterBy(rows, r)} className="truncate hover:text-accent-ink hover:underline" title="Filter to this">
                      {label(rows, r)}
                    </button>
                  </th>
                  {data.matrix[ri].map((value, ci) => {
                    const intensity = share(value, ri, ci);
                    return (
                      <td
                        key={ci}
                        title={`${label(rows, r)} × ${label(cols, data.cols[ci])}: ${formatNumber(value)}`}
                        className="tabular rounded px-1.5 py-1 text-right"
                        style={{
                          // Single-hue sequential scale that recedes into the surface near zero.
                          background: value
                            ? `color-mix(in oklab, var(--series-1) ${Math.round(8 + intensity * 82)}%, var(--surface))`
                            : "var(--surface-2)",
                          color: intensity > 0.45 ? "#fff" : "var(--ink)",
                        }}
                      >
                        {value ? cellText(value, ri, ci) : <span className="text-muted">·</span>}
                      </td>
                    );
                  })}
                  <td className="tabular px-2 py-1 text-right font-semibold">{formatNumber(data.row_totals[ri])}</td>
                </tr>
              ))}
              <tr>
                <th className="sticky left-0 bg-surface px-2 py-1.5 text-left font-semibold">Total</th>
                {data.col_totals.map((t, i) => (
                  <td key={i} className="tabular px-1.5 py-1.5 text-right font-semibold">
                    {formatNumber(t)}
                  </td>
                ))}
                <td className="tabular px-2 py-1.5 text-right font-semibold">
                  {formatNumber(data.row_totals.reduce((a, b) => a + b, 0))}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="px-4 pb-3 text-xs text-muted">
            Top {data.rows.length} {dimensionLabel(rows).toLowerCase()} values × top {data.cols.length}{" "}
            {dimensionLabel(cols).toLowerCase()} values. Darker = larger. Click a header to filter.
          </p>
        </div>
      )}
    </Card>
  );
}
