"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronRight, Eye, History, Lightbulb, Search, Star, Table2, Trash2, View } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { formatBytes, formatCompact, formatDuration } from "@/lib/format";
import { EXAMPLES, type HistoryEntry, type SavedQuery, type TableInfo } from "@/lib/query";
import { ErrorBox, Loading, Segmented } from "../ui";

type Panel = "schema" | "saved" | "history" | "examples";

export function QuerySidebar({
  schema,
  schemaLoading,
  schemaError,
  onInsert,
  onOpen,
  onPreview,
  onOpenSaved,
}: {
  schema: TableInfo[];
  schemaLoading: boolean;
  schemaError: unknown;
  onInsert: (text: string) => void;
  onOpen: (sql: string, title?: string) => void;
  onPreview: (table: TableInfo) => void;
  onOpenSaved: (saved: SavedQuery) => void;
}) {
  const [panel, setPanel] = useState<Panel>("schema");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line p-2">
        <Segmented
          size="sm"
          value={panel}
          onChange={setPanel}
          options={[
            { value: "schema", label: <Table2 size={13} />, title: "Schema" },
            { value: "saved", label: <Star size={13} />, title: "Saved queries" },
            { value: "history", label: <History size={13} />, title: "History" },
            { value: "examples", label: <Lightbulb size={13} />, title: "Examples" },
          ]}
        />
        <span className="ml-2 text-xs font-medium capitalize text-ink-2">{panel}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {panel === "schema" && (
          <SchemaPanel schema={schema} loading={schemaLoading} error={schemaError} onInsert={onInsert} onPreview={onPreview} />
        )}
        {panel === "saved" && <SavedPanel onOpenSaved={onOpenSaved} />}
        {panel === "history" && <HistoryPanel onOpen={onOpen} />}
        {panel === "examples" && (
          <ul className="py-1">
            {EXAMPLES.map((example) => (
              <li key={example.title}>
                <button
                  onClick={() => onOpen(example.sql, example.title)}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-surface-hover"
                >
                  {example.title}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function SchemaPanel({
  schema,
  loading,
  error,
  onInsert,
  onPreview,
}: {
  schema: TableInfo[];
  loading: boolean;
  error: unknown;
  onInsert: (text: string) => void;
  onPreview: (table: TableInfo) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({ "osm_app.companies": true });

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return schema;
    return schema.filter(
      (t) => `${t.schema}.${t.name}`.toLowerCase().includes(term) || t.columns.some((c) => c.name.toLowerCase().includes(term)),
    );
  }, [schema, search]);

  const schemas = [...new Set(filtered.map((t) => t.schema))];

  return (
    <div>
      <label className="relative m-2 flex items-center">
        <Search size={13} className="pointer-events-none absolute left-2 text-muted" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Tables & columns…"
          className="h-7 w-full rounded-md border border-line-strong bg-surface pl-7 text-xs outline-none focus:border-accent"
        />
      </label>
      {loading && <Loading />}
      {error ? <ErrorBox error={error} className="m-2" /> : null}
      {schemas.map((schemaName) => (
        <div key={schemaName} className="pb-2">
          <div className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{schemaName}</div>
          {filtered
            .filter((t) => t.schema === schemaName)
            .map((table) => {
              const key = `${table.schema}.${table.name}`;
              const expanded = open[key] || (search.trim().length > 0 && !key.includes(search.trim().toLowerCase()));
              return (
                <div key={key}>
                  <div className="group flex items-center gap-1 px-1.5 hover:bg-surface-hover">
                    <button
                      aria-label={expanded ? "Collapse" : "Expand"}
                      onClick={() => setOpen({ ...open, [key]: !open[key] })}
                      className="p-0.5 text-muted"
                    >
                      <ChevronRight size={13} className={clsx("transition-transform", expanded && "rotate-90")} />
                    </button>
                    {table.kind === "view" ? <View size={13} className="text-muted" /> : <Table2 size={13} className="text-muted" />}
                    <button
                      onClick={() => onInsert(key)}
                      title={`Insert ${key}`}
                      className="min-w-0 flex-1 truncate py-1 text-left text-sm"
                    >
                      {table.name}
                    </button>
                    <span className="text-[10px] text-muted" title={`~${table.estimated_rows} rows · ${formatBytes(table.size_bytes)}`}>
                      {formatCompact(table.estimated_rows)}
                    </span>
                    <button
                      onClick={() => onPreview(table)}
                      title="Preview first 100 rows"
                      className="rounded p-0.5 text-muted opacity-0 hover:text-ink group-hover:opacity-100"
                    >
                      <Eye size={13} />
                    </button>
                  </div>
                  {expanded && (
                    <ul className="pb-1 pl-8">
                      {table.columns.map((column) => (
                        <li key={column.name}>
                          <button
                            onClick={() => onInsert(column.name)}
                            className="flex w-full items-baseline justify-between gap-2 py-0.5 pr-2 text-left text-xs hover:text-accent-ink"
                          >
                            <span className="truncate font-mono">{column.name}</span>
                            <span className="shrink-0 truncate text-[10px] text-muted">{column.type}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
        </div>
      ))}
    </div>
  );
}

function SavedPanel({ onOpenSaved }: { onOpenSaved: (saved: SavedQuery) => void }) {
  const client = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["saved-queries"], queryFn: () => api<SavedQuery[]>("/api/query/saved") });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/query/saved/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["saved-queries"] }),
  });

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} className="m-2" />;
  if (!data?.length) return <p className="p-3 text-sm text-muted">No saved queries yet. Press ⌘S / Ctrl+S in the editor to save one.</p>;

  return (
    <ul className="py-1">
      {data.map((saved) => (
        <li key={saved.id} className="group flex items-start gap-1 px-1 hover:bg-surface-hover">
          <button onClick={() => onOpenSaved(saved)} className="min-w-0 flex-1 px-2 py-1.5 text-left">
            <div className="truncate text-sm">{saved.name}</div>
            <div className="truncate font-mono text-[11px] text-muted">{saved.sql.replace(/\s+/g, " ")}</div>
          </button>
          <button
            aria-label={`Delete ${saved.name}`}
            onClick={() => confirm(`Delete saved query "${saved.name}"?`) && remove.mutate(saved.id)}
            className="mt-1.5 rounded p-1 text-muted opacity-0 hover:text-critical group-hover:opacity-100"
          >
            <Trash2 size={13} />
          </button>
        </li>
      ))}
    </ul>
  );
}

function HistoryPanel({ onOpen }: { onOpen: (sql: string) => void }) {
  const client = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["query-history"],
    queryFn: () => api<HistoryEntry[]>("/api/query/history?limit=200"),
  });
  const clear = useMutation({
    mutationFn: () => api("/api/query/history", { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["query-history"] }),
  });

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} className="m-2" />;
  if (!data?.length) return <p className="p-3 text-sm text-muted">Queries you run appear here.</p>;

  return (
    <div>
      <ul className="py-1">
        {data.map((entry) => (
          <li key={entry.id}>
            <button onClick={() => onOpen(entry.sql)} className="w-full px-3 py-1.5 text-left hover:bg-surface-hover" title={entry.error ?? entry.sql}>
              <div className="truncate font-mono text-xs">{entry.sql.replace(/\s+/g, " ")}</div>
              <div className="mt-0.5 flex gap-2 text-[11px] text-muted">
                <span className={clsx(entry.status !== "ok" && "font-medium text-critical")}>
                  {entry.status === "ok" ? `${entry.row_count ?? 0} rows` : entry.status}
                </span>
                {entry.duration_ms != null && <span>{formatDuration(entry.duration_ms)}</span>}
                {!entry.read_only && <span className="text-critical">write</span>}
                <span className="ml-auto">{new Date(entry.ran_at).toLocaleString()}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>
      <div className="border-t border-line p-2">
        <button
          onClick={() => confirm("Clear the whole query history?") && clear.mutate()}
          className="w-full rounded-md py-1 text-xs text-ink-2 hover:bg-surface-hover"
        >
          Clear history
        </button>
      </div>
    </div>
  );
}
