"use client";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompact, formatNumber } from "@/lib/format";
import { isNumericType, type ColumnInfo } from "@/lib/query";
import { Empty, Segmented, Select } from "../ui";
import { cellText } from "./results-grid";

const MAX_POINTS = 500;
// Fixed categorical order from the validated palette; never cycled.
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];

type Kind = "bar" | "line" | "area";

export function ResultChart({ columns, rows }: { columns: ColumnInfo[]; rows: unknown[][] }) {
  const numeric = columns.map((c, i) => ({ ...c, index: i })).filter((c) => isNumericType(c.type));
  const firstText = columns.findIndex((c) => !isNumericType(c.type));

  const [kind, setKind] = useState<Kind>("bar");
  const [x, setX] = useState(String(firstText >= 0 ? firstText : 0));
  const [ys, setYs] = useState<number[]>(() => numeric.filter((c) => c.index !== (firstText >= 0 ? firstText : 0)).slice(0, 1).map((c) => c.index));

  const data = useMemo(
    () =>
      rows.slice(0, MAX_POINTS).map((row) => {
        const point: Record<string, unknown> = { __x: cellText(row[Number(x)]) };
        for (const y of ys) point[columns[y].name] = typeof row[y] === "number" ? row[y] : Number(row[y]);
        return point;
      }),
    [rows, x, ys, columns],
  );

  if (!numeric.length) return <Empty>Charts need at least one numeric column in the result</Empty>;

  const toggleY = (index: number) =>
    setYs(ys.includes(index) ? ys.filter((y) => y !== index) : ys.length < SERIES.length ? [...ys, index] : ys);

  const Chart = kind === "bar" ? BarChart : kind === "line" ? LineChart : AreaChart;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          size="sm"
          value={kind}
          onChange={setKind}
          options={[
            { value: "bar", label: "Bar" },
            { value: "line", label: "Line" },
            { value: "area", label: "Area" },
          ]}
        />
        <Select label="X axis" value={x} onChange={setX} options={columns.map((c, i) => ({ value: String(i), label: c.name }))} />
        <span className="text-xs text-ink-2">Y (up to {SERIES.length}):</span>
        {numeric
          .filter((c) => String(c.index) !== x)
          .map((c) => (
            <label key={c.index} className="inline-flex items-center gap-1 text-xs">
              <input type="checkbox" checked={ys.includes(c.index)} onChange={() => toggleY(c.index)} className="accent-[var(--accent)]" />
              {c.name}
            </label>
          ))}
        {rows.length > MAX_POINTS && <span className="text-xs text-muted">Charting first {MAX_POINTS} rows</span>}
      </div>
      {ys.length === 0 ? (
        <Empty>Pick at least one numeric column for the Y axis</Empty>
      ) : (
        <div className="min-h-0 flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <Chart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="__x" tickLine={false} axisLine={{ stroke: "var(--axis)" }} tick={{ fill: "var(--muted)", fontSize: 11 }} minTickGap={12} />
              <YAxis tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} tickFormatter={(v) => formatCompact(v)} width={52} />
              <Tooltip
                cursor={{ fill: "var(--surface-hover)", stroke: "var(--axis)" }}
                contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
                labelStyle={{ color: "var(--ink)", fontWeight: 600 }}
                itemStyle={{ color: "var(--ink-2)" }}
                formatter={(value) => formatNumber(Number(value))}
              />
              {ys.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value) => <span style={{ color: "var(--ink-2)" }}>{value}</span>} />}
              {ys.map((y, i) =>
                kind === "bar" ? (
                  <Bar key={y} dataKey={columns[y].name} fill={SERIES[i]} radius={[4, 4, 0, 0]} maxBarSize={40} />
                ) : kind === "line" ? (
                  <Line key={y} dataKey={columns[y].name} stroke={SERIES[i]} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                ) : (
                  <Area key={y} dataKey={columns[y].name} stroke={SERIES[i]} fill={SERIES[i]} fillOpacity={0.15} strokeWidth={2} />
                ),
              )}
            </Chart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
