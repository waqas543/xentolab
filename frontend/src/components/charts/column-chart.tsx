"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatCompact, formatNumber } from "@/lib/format";
import { Empty } from "../ui";

export interface ColumnDatum {
  label: string;
  value: number;
}

/** Vertical bars for ordered categories (years, score buckets). Single series. */
export function ColumnChart({
  data,
  height = 220,
  valueLabel = "Companies",
  onSelect,
}: {
  data: ColumnDatum[];
  height?: number;
  valueLabel?: string;
  onSelect?: (label: string) => void;
}) {
  if (!data.length) return <Empty>No data for these filters</Empty>;

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -12 }} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={{ stroke: "var(--axis)" }}
            tick={{ fill: "var(--muted)", fontSize: 11 }}
            interval="preserveStartEnd"
            minTickGap={8}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--muted)", fontSize: 11 }}
            tickFormatter={(v) => formatCompact(v)}
            width={48}
          />
          <Tooltip
            cursor={{ fill: "var(--surface-hover)" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <div className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-card">
                  <div className="font-medium text-ink">{label}</div>
                  <div className="tabular text-ink-2">
                    {valueLabel}: {formatNumber(payload[0].value as number)}
                  </div>
                </div>
              ) : null
            }
          />
          <Bar
            dataKey="value"
            fill="var(--series-1)"
            radius={[4, 4, 0, 0]}
            maxBarSize={36}
            cursor={onSelect ? "pointer" : undefined}
            onClick={onSelect ? (d) => onSelect(String((d as unknown as ColumnDatum).label)) : undefined}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
