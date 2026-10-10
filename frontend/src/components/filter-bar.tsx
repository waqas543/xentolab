"use client";

import clsx from "clsx";
import { Search, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  activeFilterCount,
  FLAG_KEYS,
  FLAG_LABELS,
  LIST_KEYS,
  LIST_LABELS,
  useFilters,
  type FlagKey,
  type ListKey,
} from "@/lib/filters";
import { humanize } from "@/lib/format";
import { useDebounced, useFilterOptions } from "@/lib/hooks";
import type { Option } from "@/lib/types";
import { MultiSelect } from "./multi-select";

/** One filter row that scopes every chart and table on the page. */
export function FilterBar({ className }: { className?: string }) {
  const { filters, setFilters, clearFilters } = useFilters();
  const { data: options } = useFilterOptions();
  const [search, setSearch] = useState(filters.q ?? "");
  const debounced = useDebounced(search, 350);
  const [showMore, setShowMore] = useState(false);

  useEffect(() => {
    if ((debounced || undefined) !== filters.q) setFilters({ q: debounced || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const [urlQuery, setUrlQuery] = useState(filters.q);
  if (urlQuery !== filters.q) {
    setUrlQuery(filters.q);
    if ((filters.q ?? "") !== debounced) setSearch(filters.q ?? "");
  }

  // Narrow child options to the selected parents (categories by group, districts by province).
  const categories = useMemo(
    () => narrow(options?.categories ?? [], filters.group),
    [options?.categories, filters.group],
  );
  const districts = useMemo(
    () => narrow(options?.districts ?? [], filters.province),
    [options?.districts, filters.province],
  );

  const listOptions: Record<ListKey, Option[]> = {
    group: options?.groups ?? [],
    category: categories,
    province: options?.provinces ?? [],
    district: districts,
    city: options?.cities ?? [],
    brand: options?.brands ?? [],
  };

  const count = activeFilterCount(filters);

  const cycleFlag = (key: FlagKey) => {
    const current = filters[key];
    setFilters({ [key]: current === undefined ? true : current ? false : undefined });
  };

  return (
    <div className={clsx("flex flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <label className="relative flex h-8 min-w-48 flex-1 items-center sm:max-w-72">
          <Search size={14} className="pointer-events-none absolute left-2.5 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name or brand…"
            aria-label="Search companies"
            className="h-full w-full rounded-md border border-line-strong bg-surface pl-8 pr-7 text-sm outline-none placeholder:text-muted focus:border-accent"
          />
          {search && (
            <button aria-label="Clear search" onClick={() => setSearch("")} className="absolute right-2 text-muted hover:text-ink">
              <X size={14} />
            </button>
          )}
        </label>

        {(["group", "category", "province", "district", "city"] as ListKey[]).map((key) => (
          <MultiSelect
            key={key}
            label={LIST_LABELS[key]}
            options={listOptions[key]}
            selected={filters[key]}
            onChange={(values) => setFilters({ [key]: values })}
            humanizeValues={key === "category"}
          />
        ))}

        <button
          onClick={() => setShowMore(!showMore)}
          className={clsx(
            "inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm",
            showMore ? "bg-surface-hover text-ink" : "text-ink-2 hover:bg-surface-hover",
          )}
        >
          <SlidersHorizontal size={14} /> More
        </button>

        {count > 0 && (
          <button onClick={clearFilters} className="inline-flex h-8 items-center gap-1 px-1.5 text-sm text-accent-ink hover:underline">
            Clear all ({count})
          </button>
        )}
      </div>

      {showMore && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-2 p-2">
          <MultiSelect
            label="Brand"
            options={listOptions.brand}
            selected={filters.brand}
            onChange={(values) => setFilters({ brand: values })}
          />
          <span className="mx-1 h-5 w-px bg-line-strong" />
          <span className="text-xs text-muted">Has (click: yes → no → any)</span>
          {FLAG_KEYS.map((key) => (
            <TriState key={key} label={FLAG_LABELS[key]} value={filters[key]} onClick={() => cycleFlag(key)} />
          ))}
          <TriState
            label="Brand / chain"
            value={filters.branded}
            onClick={() =>
              setFilters({ branded: filters.branded === undefined ? true : filters.branded ? false : undefined })
            }
          />
          <span className="mx-1 h-5 w-px bg-line-strong" />
          <label className="inline-flex items-center gap-1.5 text-xs text-ink-2">
            Min. completeness
            <select
              value={filters.min_completeness ?? 0}
              onChange={(e) => setFilters({ min_completeness: Number(e.target.value) || undefined })}
              className="h-7 rounded-md border border-line-strong bg-surface px-1.5 text-sm text-ink"
            >
              {[0, 20, 40, 60, 80, 100].map((v) => (
                <option key={v} value={v}>
                  {v === 0 ? "Any" : `${v}%+`}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <ActiveChips />
    </div>
  );
}

function TriState({ label, value, onClick }: { label: string; value: boolean | undefined; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={value !== undefined}
      className={clsx(
        "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs font-medium",
        value === undefined && "border-line-strong bg-surface text-ink-2 hover:bg-surface-hover",
        value === true && "border-accent bg-accent text-white",
        value === false && "border-ink-2 bg-surface text-ink line-through decoration-1",
      )}
    >
      {value === false ? `No ${label.toLowerCase()}` : label}
    </button>
  );
}

function ActiveChips() {
  const { filters, setFilters } = useFilters();
  const chips: { key: string; label: string; remove: () => void }[] = [];

  if (filters.q) chips.push({ key: "q", label: `“${filters.q}”`, remove: () => setFilters({ q: undefined }) });
  for (const key of LIST_KEYS) {
    for (const value of filters[key]) {
      chips.push({
        key: `${key}:${value}`,
        label: `${LIST_LABELS[key]}: ${key === "category" ? humanize(value) : value}`,
        remove: () => setFilters({ [key]: filters[key].filter((v) => v !== value) }),
      });
    }
  }
  for (const key of FLAG_KEYS) {
    if (filters[key] !== undefined) {
      chips.push({
        key,
        label: `${filters[key] ? "Has" : "No"} ${FLAG_LABELS[key].toLowerCase()}`,
        remove: () => setFilters({ [key]: undefined }),
      });
    }
  }
  if (filters.branded !== undefined) {
    chips.push({ key: "branded", label: filters.branded ? "Chains only" : "Independents only", remove: () => setFilters({ branded: undefined }) });
  }
  if (filters.min_completeness) {
    chips.push({
      key: "min",
      label: `Completeness ≥ ${filters.min_completeness}%`,
      remove: () => setFilters({ min_completeness: undefined }),
    });
  }

  if (!chips.length) return null;

  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.map((chip) => (
        <span key={chip.key} className="inline-flex items-center gap-1 rounded-full bg-accent-soft/50 py-0.5 pl-2.5 pr-1 text-xs text-ink">
          {chip.label}
          <button aria-label={`Remove ${chip.label}`} onClick={chip.remove} className="rounded-full p-0.5 hover:bg-accent-soft">
            <X size={12} />
          </button>
        </span>
      ))}
    </div>
  );
}

function narrow(options: Option[], parents: string[]) {
  return parents.length ? options.filter((o) => o.parent && parents.includes(o.parent)) : options;
}
