"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import clsx from "clsx";
import { ArrowDown, ArrowUp, Copy, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { isNumericType, type ColumnInfo } from "@/lib/query";

const ROW_HEIGHT = 28;
const INDEX_WIDTH = 56;

export function cellText(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ResultsGrid({ columns, rows }: { columns: ColumnInfo[]; rows: unknown[][] }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [sort, setSort] = useState<{ index: number; desc: boolean } | null>(null);
  const [search, setSearch] = useState("");
  const [cell, setCell] = useState<{ row: number; col: number } | null>(null);

  const widths = useMemo(
    () =>
      columns.map((column, ci) => {
        let longest = column.name.length + column.type.length * 0.6 + 4;
        for (let i = 0; i < Math.min(rows.length, 200); i++) longest = Math.max(longest, cellText(rows[i][ci]).length);
        return Math.min(420, Math.max(80, Math.round(longest * 7.4 + 20)));
      }),
    [columns, rows],
  );

  const visible = useMemo(() => {
    let indexes = rows.map((_, i) => i);
    const term = search.trim().toLowerCase();
    if (term) indexes = indexes.filter((i) => rows[i].some((v) => cellText(v).toLowerCase().includes(term)));
    if (sort) {
      const { index, desc } = sort;
      indexes.sort((a, b) => {
        const x = rows[a][index];
        const y = rows[b][index];
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        const result = typeof x === "number" && typeof y === "number" ? x - y : cellText(x).localeCompare(cellText(y), undefined, { numeric: true });
        return desc ? -result : result;
      });
    }
    return indexes;
  }, [rows, search, sort]);

  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  });

  const totalWidth = INDEX_WIDTH + widths.reduce((a, b) => a + b, 0);
  const template = `${INDEX_WIDTH}px ${widths.map((w) => `${w}px`).join(" ")}`;

  const toggleSort = (index: number) =>
    setSort(!sort || sort.index !== index ? { index, desc: false } : sort.desc ? null : { index, desc: true });

  const selectedValue = cell ? rows[cell.row]?.[cell.col] : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter rows…"
          className="h-7 w-56 rounded-md border border-line-strong bg-surface px-2 text-xs outline-none focus:border-accent"
        />
        {search && (
          <span className="text-xs text-muted">
            {visible.length} of {rows.length} rows
          </span>
        )}
      </div>

      <div ref={parentRef} className="relative min-h-0 flex-1 overflow-auto font-mono text-xs">
        <div style={{ width: totalWidth, minWidth: "100%" }}>
          <div
            className="sticky top-0 z-10 grid border-b border-line bg-surface-2"
            style={{ gridTemplateColumns: template }}
          >
            <div className="px-2 py-1.5 text-right text-muted">#</div>
            {columns.map((column, ci) => (
              <button
                key={ci}
                onClick={() => toggleSort(ci)}
                title={`${column.name} (${column.type}) — click to sort`}
                className={clsx(
                  "flex items-center gap-1 overflow-hidden border-l border-line px-2 py-1.5 text-left font-sans hover:bg-surface-hover",
                  isNumericType(column.type) && "justify-end",
                )}
              >
                <span className="truncate font-semibold text-ink">{column.name}</span>
                <span className="shrink-0 text-[10px] text-muted">{column.type}</span>
                {sort?.index === ci && (sort.desc ? <ArrowDown size={11} /> : <ArrowUp size={11} />)}
              </button>
            ))}
          </div>

          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const rowIndex = visible[item.index];
              const row = rows[rowIndex];
              return (
                <div
                  key={item.key}
                  className="absolute left-0 grid w-full border-b border-line/50 hover:bg-surface-hover"
                  style={{ top: item.start, height: ROW_HEIGHT, gridTemplateColumns: template }}
                >
                  <div className="tabular px-2 py-1.5 text-right text-muted">{rowIndex + 1}</div>
                  {row.map((value, ci) => {
                    const active = cell?.row === rowIndex && cell.col === ci;
                    return (
                      <div
                        key={ci}
                        onClick={() => setCell({ row: rowIndex, col: ci })}
                        className={clsx(
                          "cursor-default truncate border-l border-line/50 px-2 py-1.5",
                          value === null && "italic text-muted",
                          isNumericType(columns[ci].type) && "tabular text-right",
                          active && "bg-accent-soft/60 outline outline-1 -outline-offset-1 outline-accent",
                        )}
                        dir="auto"
                      >
                        {cellText(value)}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {cell && (
        <div className="max-h-48 shrink-0 overflow-auto border-t border-line bg-surface-2 px-3 py-2">
          <div className="mb-1 flex items-center justify-between text-xs text-muted">
            <span>
              Row {cell.row + 1} · <span className="font-semibold text-ink">{columns[cell.col].name}</span> ({columns[cell.col].type})
            </span>
            <span className="flex gap-1">
              <button
                title="Copy value"
                onClick={() => navigator.clipboard.writeText(cellText(selectedValue))}
                className="rounded p-1 hover:bg-surface-hover"
              >
                <Copy size={13} />
              </button>
              <button title="Close" onClick={() => setCell(null)} className="rounded p-1 hover:bg-surface-hover">
                <X size={13} />
              </button>
            </span>
          </div>
          <pre className="whitespace-pre-wrap break-all font-mono text-xs" dir="auto">
            {typeof selectedValue === "object" && selectedValue !== null
              ? JSON.stringify(selectedValue, null, 2)
              : cellText(selectedValue)}
          </pre>
        </div>
      )}
    </div>
  );
}
