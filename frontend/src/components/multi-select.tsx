"use client";

import clsx from "clsx";
import { Check, ChevronDown, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatNumber, humanize } from "@/lib/format";
import type { Option } from "@/lib/types";

const VISIBLE_LIMIT = 300;

export function MultiSelect({
  label,
  options,
  selected,
  onChange,
  humanizeValues = false,
}: {
  label: string;
  options: Option[];
  selected: string[];
  onChange: (values: string[]) => void;
  humanizeValues?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    inputRef.current?.focus();
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const display = (value: string) => (humanizeValues ? humanize(value) : value);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const matches = term
      ? options.filter((o) => o.value.toLowerCase().includes(term) || display(o.value).toLowerCase().includes(term))
      : options;
    // Selected values first, so they are always reachable.
    return [...matches.filter((o) => selected.includes(o.value)), ...matches.filter((o) => !selected.includes(o.value))];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, search, selected]);

  const toggle = (value: string) =>
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className={clsx(
          "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-sm transition-colors",
          selected.length
            ? "border-accent/50 bg-accent-soft/40 text-ink"
            : "border-line-strong bg-surface text-ink-2 hover:bg-surface-hover",
        )}
      >
        {label}
        {selected.length > 0 && (
          <span className="rounded-full bg-accent px-1.5 text-[11px] font-semibold text-white">{selected.length}</span>
        )}
        <ChevronDown size={14} className="text-muted" />
      </button>

      {open && (
        <div className="absolute left-0 z-[1200] mt-1 w-72 rounded-lg border border-line bg-surface shadow-card">
          <div className="flex items-center gap-2 border-b border-line px-2.5">
            <Search size={14} className="text-muted" />
            <input
              ref={inputRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={`Search ${label.toLowerCase()}…`}
              className="h-9 w-full bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>
          <ul className="max-h-72 overflow-auto py-1" role="listbox" aria-multiselectable>
            {filtered.slice(0, VISIBLE_LIMIT).map((o) => {
              const checked = selected.includes(o.value);
              return (
                <li key={`${o.parent ?? ""}:${o.value}`}>
                  <button
                    role="option"
                    aria-selected={checked}
                    onClick={() => toggle(o.value)}
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-surface-hover"
                  >
                    <span
                      className={clsx(
                        "grid size-4 shrink-0 place-items-center rounded border",
                        checked ? "border-accent bg-accent text-white" : "border-line-strong",
                      )}
                    >
                      {checked && <Check size={12} strokeWidth={3} />}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      {display(o.value)}
                      {o.parent && <span className="ml-1.5 text-xs text-muted">{o.parent}</span>}
                    </span>
                    <span className="tabular text-xs text-muted">{formatNumber(o.count)}</span>
                  </button>
                </li>
              );
            })}
            {filtered.length > VISIBLE_LIMIT && (
              <li className="px-2.5 py-1.5 text-xs text-muted">
                {formatNumber(filtered.length - VISIBLE_LIMIT)} more — refine the search
              </li>
            )}
            {filtered.length === 0 && <li className="px-2.5 py-3 text-sm text-muted">No matches</li>}
          </ul>
          {selected.length > 0 && (
            <div className="border-t border-line p-1.5">
              <button
                onClick={() => onChange([])}
                className="flex w-full items-center justify-center gap-1 rounded-md py-1 text-xs text-ink-2 hover:bg-surface-hover"
              >
                <X size={12} /> Clear {label.toLowerCase()}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
