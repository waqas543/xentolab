"use client";

import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Download } from "lucide-react";
import { useState } from "react";
import { CompanyDrawer } from "@/components/company-drawer";
import { FilterBar } from "@/components/filter-bar";
import { Card, ErrorBox, Loading, PageHeader, Select, Spinner } from "@/components/ui";
import { API_URL } from "@/lib/api";
import { useFilters, useUrlParam } from "@/lib/filters";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { useApi, withQuery } from "@/lib/hooks";
import type { CompanyPage, CompanyRef } from "@/lib/types";

const COLUMNS: { key: string; label: string; sortable?: boolean; className?: string }[] = [
  { key: "name", label: "Name", sortable: true },
  { key: "category", label: "Category", sortable: true },
  { key: "city", label: "City", sortable: true },
  { key: "district", label: "District / province", sortable: true },
  { key: "contact", label: "Contact" },
  { key: "completeness", label: "Complete", sortable: true, className: "text-right" },
  { key: "last_edited", label: "Last edited", sortable: true, className: "text-right" },
];

export function CompaniesPage() {
  const { queryString } = useFilters();
  const [sort, setSort] = useUrlParam("sort", "name");
  const [order, setOrder] = useUrlParam("order", "asc");
  const [pageSize, setPageSize] = useState(50);
  const [selected, setSelected] = useState<CompanyRef | null>(null);

  // The page number belongs to one query; any filter/sort change starts again at page 1.
  const queryKey = `${queryString}|${sort}|${order}|${pageSize}`;
  const [paging, setPaging] = useState({ queryKey, page: 1 });
  const page = paging.queryKey === queryKey ? paging.page : 1;
  const setPage = (next: number) => setPaging({ queryKey, page: next });

  const { data, error, isLoading, isFetching } = useApi<CompanyPage>(
    withQuery("/api/companies", queryString, { sort, order, page, page_size: pageSize }),
  );

  const pages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  const sortBy = (key: string) => {
    if (sort === key) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setOrder(key === "completeness" || key === "last_edited" ? "desc" : "asc");
    }
  };

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader
        title="Companies"
        description={data ? `${formatNumber(data.total)} companies match the current filters` : "Loading…"}
        actions={
          <a
            href={`${API_URL}${withQuery("/api/companies/export", queryString)}`}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 text-sm font-medium hover:bg-surface-hover"
          >
            <Download size={14} /> Export CSV
          </a>
        }
      />
      <FilterBar />
      {error && <ErrorBox error={error} />}

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className={clsx("w-full min-w-[960px] text-sm transition-opacity", isFetching && !isLoading && "opacity-60")}>
            <thead>
              <tr className="border-b border-line bg-surface-2 text-left text-xs text-ink-2">
                {COLUMNS.map((c) => (
                  <th key={c.key} className={clsx("px-3 py-2 font-medium", c.className)}>
                    {c.sortable ? (
                      <button onClick={() => sortBy(c.key)} className="inline-flex items-center gap-1 hover:text-ink">
                        {c.label}
                        {sort === c.key && (order === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.rows.map((row) => (
                <tr
                  key={`${row.osm_type}${row.osm_id}`}
                  onClick={() => setSelected({ osm_type: row.osm_type, osm_id: row.osm_id })}
                  className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-surface-hover"
                >
                  <td className="max-w-72 px-3 py-2">
                    <div className="truncate font-medium">{row.name}</div>
                    {row.brand && <div className="truncate text-xs text-muted">Chain: {row.brand}</div>}
                    {!row.brand && row.name_local && row.name_local !== row.name && (
                      <div className="truncate text-xs text-muted" dir="auto">
                        {row.name_local}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div>{humanize(row.category)}</div>
                    <div className="text-xs text-muted">{row.category_group}</div>
                  </td>
                  <td className="px-3 py-2">
                    <div>{row.city ?? "–"}</div>
                    {row.locality && row.locality !== row.city && <div className="text-xs text-muted">{row.locality}</div>}
                  </td>
                  <td className="px-3 py-2">
                    <div>{row.district ?? "–"}</div>
                    <div className="text-xs text-muted">{row.province}</div>
                  </td>
                  <td className="max-w-56 px-3 py-2 text-xs">
                    {row.phone && <div className="truncate">{row.phone}</div>}
                    {row.website && <div className="truncate text-accent-ink">{row.website.replace(/^https?:\/\//, "")}</div>}
                    {row.email && <div className="truncate">{row.email}</div>}
                    {!row.phone && !row.website && !row.email && <span className="text-muted">–</span>}
                  </td>
                  <td className="tabular px-3 py-2 text-right">{row.completeness}%</td>
                  <td className="tabular px-3 py-2 text-right text-ink-2">{formatDate(row.last_edited)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {isLoading && <Loading />}
        {data && data.rows.length === 0 && <div className="py-10 text-center text-sm text-muted">No companies match these filters</div>}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-3 py-2">
          <Select
            label="Rows"
            value={String(pageSize)}
            onChange={(v) => setPageSize(Number(v))}
            options={[25, 50, 100, 200].map((n) => ({ value: String(n), label: String(n) }))}
          />
          <div className="flex items-center gap-2 text-sm text-ink-2">
            {isFetching && <Spinner size={14} />}
            <span className="tabular">
              Page {formatNumber(page)} of {formatNumber(pages)}
            </span>
            <button
              aria-label="Previous page"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="rounded-md border border-line-strong p-1 hover:bg-surface-hover disabled:opacity-40"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              aria-label="Next page"
              disabled={page >= pages}
              onClick={() => setPage(page + 1)}
              className="rounded-md border border-line-strong p-1 hover:bg-surface-hover disabled:opacity-40"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </Card>

      <CompanyDrawer selected={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
