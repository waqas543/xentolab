"use client";

import clsx from "clsx";
import { BarChart3, Table2 } from "lucide-react";
import { useState } from "react";
import { Card, Segmented, Spinner } from "../ui";

/** A titled chart with a Chart / Table toggle, so every value is readable without hovering. */
export function ChartCard({
  title,
  subtitle,
  chart,
  table,
  loading,
  className,
  actions,
}: {
  title: string;
  subtitle?: React.ReactNode;
  chart: React.ReactNode;
  table?: React.ReactNode;
  loading?: boolean;
  className?: string;
  actions?: React.ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");

  return (
    <Card className={clsx("flex flex-col", className)}>
      <div className="flex items-start justify-between gap-2 px-4 pt-3.5">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            {title}
            {loading && <Spinner size={13} />}
          </h2>
          {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {actions}
          {table && (
            <Segmented
              size="sm"
              value={view}
              onChange={setView}
              options={[
                { value: "chart", label: <BarChart3 size={13} />, title: "Chart" },
                { value: "table", label: <Table2 size={13} />, title: "Table" },
              ]}
            />
          )}
        </div>
      </div>
      <div className={clsx("flex-1 px-4 pb-4 pt-3 transition-opacity", loading && "opacity-60")}>
        {view === "chart" || !table ? chart : table}
      </div>
    </Card>
  );
}

export function SimpleTable({
  columns,
  rows,
}: {
  columns: { key: string; label: string; numeric?: boolean }[];
  rows: Record<string, React.ReactNode>[];
}) {
  return (
    <div className="max-h-80 overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-line text-left text-xs text-muted">
            {columns.map((c) => (
              <th key={c.key} className={clsx("py-1.5 pr-3 font-medium", c.numeric && "text-right")}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0">
              {columns.map((c) => (
                <td key={c.key} className={clsx("py-1.5 pr-3", c.numeric && "tabular text-right")}>
                  {row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
