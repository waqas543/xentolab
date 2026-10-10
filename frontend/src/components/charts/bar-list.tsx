"use client";

import clsx from "clsx";
import { formatNumber } from "@/lib/format";
import { Empty } from "../ui";

export interface BarItem {
  key: string;
  label: React.ReactNode;
  value: number;
  /** Optional secondary text shown after the label. */
  hint?: React.ReactNode;
  /** Pre-formatted value; defaults to a formatted integer. */
  display?: string;
}

/**
 * Ranked horizontal bars (single series). Each row is a button when `onSelect`
 * is given, so clicking a bar drills into that value.
 */
export function BarList({
  items,
  onSelect,
  selected = [],
  max,
  total,
}: {
  items: BarItem[];
  onSelect?: (key: string) => void;
  selected?: string[];
  /** Scale maximum; defaults to the largest value (use 100 for percentages). */
  max?: number;
  /** When set, the hover title shows each value's share of this total. */
  total?: number;
}) {
  if (!items.length) return <Empty>No data for these filters</Empty>;

  const scale = max ?? Math.max(...items.map((i) => i.value), 1);
  const dimOthers = selected.length > 0;

  return (
    <ul className="flex flex-col">
      {items.map((item) => {
        const isSelected = selected.includes(item.key);
        const width = (item.value / scale) * 100;
        const share = total ? ` · ${((item.value / total) * 100).toFixed(1)}% of total` : "";
        const content = (
          <>
            <span className="flex min-w-0 items-baseline gap-1.5 text-sm">
              <span className="truncate text-ink">{item.label}</span>
              {item.hint && <span className="shrink-0 text-xs text-muted">{item.hint}</span>}
            </span>
            <span className="tabular text-right text-sm text-ink-2">{item.display ?? formatNumber(item.value)}</span>
            <span className="col-span-2 h-1.5 rounded-full bg-surface-2">
              <span
                className={clsx(
                  "block h-full rounded-full transition-[width,opacity] duration-300",
                  dimOthers && !isSelected ? "bg-accent/35" : "bg-accent",
                )}
                style={{ width: `${Math.max(width, item.value > 0 ? 0.8 : 0)}%` }}
              />
            </span>
          </>
        );

        const rowClass =
          "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-md px-1.5 py-1.5 text-left";

        return (
          <li key={item.key}>
            {onSelect ? (
              <button
                onClick={() => onSelect(item.key)}
                title={`${formatNumber(item.value)}${share} — click to filter`}
                aria-pressed={isSelected}
                className={clsx(rowClass, "hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-accent")}
              >
                {content}
              </button>
            ) : (
              <div className={rowClass} title={`${formatNumber(item.value)}${share}`}>
                {content}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
