"use client";

import type { EditorView } from "@codemirror/view";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertTriangle,
  Ban,
  ClipboardCopy,
  Download,
  FileJson,
  Gauge,
  Lock,
  PanelLeft,
  Play,
  Plus,
  Save,
  Sparkles,
  Square,
  Unlock,
  X,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { format as formatSql } from "sql-formatter";
import { ResultChart } from "@/components/query/result-chart";
import { cellText, ResultsGrid } from "@/components/query/results-grid";
import { QuerySidebar } from "@/components/query/sidebar";
import { SqlEditor } from "@/components/query/sql-editor";
import { Button, Empty, Segmented, Select, Spinner } from "@/components/ui";
import { api, ApiError, downloadPost, postJson } from "@/lib/api";
import { formatDuration, formatNumber } from "@/lib/format";
import type { QueryError, QueryResult, SavedQuery, TableInfo } from "@/lib/query";

interface Tab {
  id: string;
  title: string;
  sql: string;
  savedId?: number;
}

type View = "results" | "chart" | "plan" | "messages";

interface Run {
  running?: boolean;
  queryId?: string;
  result?: QueryResult;
  error?: QueryError;
  plan?: string;
  view: View;
  startedAt?: number;
}

const TABS_KEY = "query-runner:tabs";
const SETTINGS_KEY = "query-runner:settings";
const DESTRUCTIVE = /\b(drop|truncate|delete|update|alter|insert|create|grant|revoke)\b/i;

const DEFAULT_SQL = `-- Write any SQL. ⌘/Ctrl+Enter runs the query (or just the selection).
SELECT category_group, count(*) AS companies
FROM osm_app.companies
GROUP BY 1
ORDER BY 2 DESC;`;

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

function loadTabs(): { tabs: Tab[]; active: string } {
  try {
    const saved = JSON.parse(localStorage.getItem(TABS_KEY) ?? "null");
    if (saved?.tabs?.length) return saved;
  } catch {}
  const id = newId();
  return { tabs: [{ id, title: "Query 1", sql: DEFAULT_SQL }], active: id };
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
    if (saved) return { maxRows: Number(saved.maxRows) || 1000, timeout: Number(saved.timeout) || 60 };
  } catch {}
  return { maxRows: 1000, timeout: 60 };
}

function asQueryError(error: unknown): QueryError {
  if (error instanceof ApiError && error.body && typeof error.body === "object" && "message" in error.body) {
    return error.body as QueryError;
  }
  return {
    status: "error",
    message: error instanceof Error ? error.message : String(error),
    detail: null,
    hint: null,
    position: null,
    sqlstate: null,
  };
}

export function QueryRunner() {
  const client = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Restore tabs, and open ?sql=... (from "Open in query runner") in a new tab.
  const [initial] = useState(() => {
    const restored = loadTabs();
    const incoming = searchParams.get("sql");
    if (incoming) {
      const id = newId();
      restored.tabs.push({ id, title: `Query ${restored.tabs.length + 1}`, sql: incoming });
      restored.active = id;
    }
    if (!restored.tabs.some((t) => t.id === restored.active)) restored.active = restored.tabs[0].id;
    return { ...restored, settings: loadSettings() };
  });

  const [tabs, setTabs] = useState<Tab[]>(initial.tabs);
  const [activeId, setActiveId] = useState(initial.active);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [readOnly, setReadOnly] = useState(true);
  const [maxRows, setMaxRows] = useState(initial.settings.maxRows);
  const [timeout, setTimeoutSeconds] = useState(initial.settings.timeout);
  const [editorHeight, setEditorHeight] = useState(260);
  const [showSidebar, setShowSidebar] = useState(true);
  const [saving, setSaving] = useState<{ name: string } | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Drop ?sql= from the URL once it has been opened.
  useEffect(() => {
    if (searchParams.get("sql")) router.replace(pathname);
  }, [pathname, router, searchParams]);

  useEffect(() => {
    if (!tabs.length) return;
    try {
      localStorage.setItem(TABS_KEY, JSON.stringify({ tabs, active: activeId }));
    } catch {}
  }, [tabs, activeId]);

  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ maxRows, timeout }));
    } catch {}
  }, [maxRows, timeout]);

  const schema = useQuery({
    queryKey: ["schema"],
    queryFn: () => api<TableInfo[]>("/api/query/schema"),
    staleTime: 5 * 60_000,
  });

  const tab = tabs.find((t) => t.id === activeId);
  const run: Run = runs[activeId] ?? { view: "results" };

  const updateRun = useCallback((id: string, patch: Partial<Run>) => {
    setRuns((current) => ({ ...current, [id]: { ...(current[id] ?? { view: "results" }), ...patch } }));
  }, []);

  const updateTab = (id: string, patch: Partial<Tab>) =>
    setTabs((current) => current.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const openTab = useCallback((sql: string, title?: string) => {
    const id = newId();
    setTabs((current) => [...current, { id, title: title ?? `Query ${current.length + 1}`, sql }]);
    setActiveId(id);
    return id;
  }, []);

  const closeTab = (id: string) => {
    if (tabs.length === 1) {
      updateTab(id, { sql: "", title: "Query 1", savedId: undefined });
      return;
    }
    const index = tabs.findIndex((t) => t.id === id);
    const next = tabs.filter((t) => t.id !== id);
    setTabs(next);
    if (id === activeId) setActiveId(next[Math.max(0, index - 1)].id);
  };

  /** The selected text if any, otherwise the whole editor, plus its offset in the document. */
  const currentStatement = () => {
    const view = viewRef.current;
    if (!view || !tab) return { text: tab?.sql ?? "", offset: 0 };
    const selection = view.state.selection.main;
    if (selection.empty) return { text: view.state.doc.toString(), offset: 0 };
    return { text: view.state.sliceDoc(selection.from, selection.to), offset: selection.from };
  };

  const jumpToError = (position: number | null, offset: number) => {
    const view = viewRef.current;
    if (!view || !position) return;
    const at = Math.min(view.state.doc.length, offset + position - 1);
    view.dispatch({ selection: { anchor: at, head: Math.min(view.state.doc.length, at + 1) }, scrollIntoView: true });
    view.focus();
  };

  const execute = async () => {
    if (!tab || run.running) return;
    const { text, offset } = currentStatement();
    if (!text.trim()) return;
    if (!readOnly && DESTRUCTIVE.test(text) && !confirm("Write mode is on and this query can change data. Run it and commit?")) return;

    const id = tab.id;
    const queryId = crypto.randomUUID();
    updateRun(id, { running: true, queryId, error: undefined, startedAt: Date.now() });

    try {
      const result = await postJson<QueryResult>("/api/query/run", {
        sql: text,
        read_only: readOnly,
        max_rows: maxRows,
        timeout_seconds: timeout,
        query_id: queryId,
      });
      setRuns((current) => {
        const previous = current[id]?.view ?? "results";
        const view: View = result.columns.length ? (previous === "messages" || previous === "plan" ? "results" : previous) : "messages";
        return { ...current, [id]: { ...current[id], running: false, result, error: undefined, view } };
      });
      if (!readOnly) client.invalidateQueries({ queryKey: ["schema"] });
    } catch (error) {
      const queryError = asQueryError(error);
      updateRun(id, { running: false, error: queryError, result: undefined, view: "results" });
      jumpToError(queryError.position, offset);
    } finally {
      client.invalidateQueries({ queryKey: ["query-history"] });
    }
  };

  const cancel = async () => {
    if (run.queryId) await api(`/api/query/cancel/${run.queryId}`, { method: "POST" }).catch(() => {});
  };

  const explain = async (analyze: boolean) => {
    if (!tab || run.running) return;
    const { text, offset } = currentStatement();
    if (!text.trim()) return;
    const id = tab.id;
    updateRun(id, { running: true, error: undefined, startedAt: Date.now() });
    try {
      const response = await postJson<{ plan: string }>("/api/query/explain", { sql: text, analyze, timeout_seconds: timeout });
      updateRun(id, { running: false, plan: response.plan, view: "plan" });
    } catch (error) {
      const queryError = asQueryError(error);
      updateRun(id, { running: false, error: queryError, view: "results" });
      jumpToError(queryError.position, offset);
    }
  };

  const formatQuery = () => {
    if (!tab) return;
    try {
      updateTab(tab.id, { sql: formatSql(tab.sql, { language: "postgresql", keywordCase: "upper", tabWidth: 2 }) });
    } catch (error) {
      alert(`Could not format: ${error instanceof Error ? error.message : error}`);
    }
  };

  const save = async (name: string) => {
    if (!tab || !name.trim()) return;
    const body = { name: name.trim(), sql: tab.sql };
    const saved = tab.savedId
      ? await api<SavedQuery>(`/api/query/saved/${tab.savedId}`, { method: "PUT", body: JSON.stringify(body) })
      : await postJson<SavedQuery>("/api/query/saved", body);
    updateTab(tab.id, { title: saved.name, savedId: saved.id });
    client.invalidateQueries({ queryKey: ["saved-queries"] });
    setSaving(null);
  };

  const insertText = (text: string) => {
    const view = viewRef.current;
    if (!view) return;
    const { from, to } = view.state.selection.main;
    view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
    view.focus();
  };

  const copyResults = (kind: "tsv" | "json") => {
    if (!run.result) return;
    const { columns, rows } = run.result;
    const text =
      kind === "tsv"
        ? [columns.map((c) => c.name).join("\t"), ...rows.map((r) => r.map((v) => (v === null ? "" : cellText(v))).join("\t"))].join("\n")
        : JSON.stringify(rows.map((r) => Object.fromEntries(columns.map((c, i) => [c.name, r[i]]))), null, 2);
    navigator.clipboard.writeText(text);
  };

  const exportCsv = async () => {
    const { text } = currentStatement();
    try {
      await downloadPost("/api/query/export", { sql: text, read_only: readOnly, timeout_seconds: 600 }, "query-result.csv");
    } catch (error) {
      alert(error instanceof Error ? error.message : String(error));
    }
  };

  // Drag the divider between editor and results.
  const startResize = (event: React.PointerEvent) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = editorHeight;
    const max = (containerRef.current?.clientHeight ?? 800) - 160;
    const move = (e: PointerEvent) => setEditorHeight(Math.min(max, Math.max(100, startHeight + e.clientY - startY)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  if (!tab) return null;

  const result = run.result;

  return (
    <div className="flex h-[calc(100dvh-53px)] min-h-0 lg:h-dvh">
      {showSidebar && (
        <aside className="hidden w-72 shrink-0 border-r border-line bg-surface md:block">
          <QuerySidebar
            schema={schema.data ?? []}
            schemaLoading={schema.isLoading}
            schemaError={schema.error}
            onInsert={insertText}
            onOpen={(sql, title) => openTab(sql, title)}
            onPreview={(table) => {
              openTab(`SELECT *\nFROM ${table.schema}.${table.name}\nLIMIT 100;`, table.name);
            }}
            onOpenSaved={(saved) => {
              const existing = tabs.find((t) => t.savedId === saved.id);
              if (existing) setActiveId(existing.id);
              else {
                const id = newId();
                setTabs((current) => [...current, { id, title: saved.name, sql: saved.sql, savedId: saved.id }]);
                setActiveId(id);
              }
            }}
          />
        </aside>
      )}

      <div ref={containerRef} className="flex min-w-0 flex-1 flex-col">
        {/* Tabs */}
        <div className="flex items-end gap-0.5 overflow-x-auto border-b border-line bg-surface-2 px-2 pt-1.5">
          <button
            onClick={() => setShowSidebar(!showSidebar)}
            title="Toggle sidebar"
            className="mb-1 mr-1 hidden rounded p-1 text-ink-2 hover:bg-surface-hover md:block"
          >
            <PanelLeft size={15} />
          </button>
          {tabs.map((t) => (
            <div
              key={t.id}
              className={clsx(
                "group flex max-w-52 items-center gap-1 rounded-t-md border border-b-0 px-2.5 py-1.5 text-sm",
                t.id === activeId ? "border-line bg-surface text-ink" : "border-transparent text-ink-2 hover:bg-surface-hover",
              )}
            >
              <button onClick={() => setActiveId(t.id)} className="flex min-w-0 items-center gap-1.5 truncate">
                {runs[t.id]?.running && <Spinner size={12} />}
                <span className="truncate">{t.title}</span>
              </button>
              <button aria-label={`Close ${t.title}`} onClick={() => closeTab(t.id)} className="rounded p-0.5 text-muted opacity-60 hover:bg-surface-hover hover:opacity-100">
                <X size={12} />
              </button>
            </div>
          ))}
          <button aria-label="New tab" onClick={() => openTab("")} className="mb-1 ml-1 rounded p-1 text-ink-2 hover:bg-surface-hover">
            <Plus size={15} />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 py-2">
          {run.running ? (
            <Button variant="danger" onClick={cancel}>
              <Square size={13} /> Cancel
              {run.startedAt && <Elapsed since={run.startedAt} />}
            </Button>
          ) : (
            <Button variant="primary" onClick={execute} title="Run (⌘/Ctrl+Enter). Runs the selection if there is one.">
              <Play size={13} /> Run <kbd className="text-[10px] opacity-75">⌘↵</kbd>
            </Button>
          )}
          <Button onClick={() => explain(false)} disabled={run.running} title="Show the query plan (⌘/Ctrl+Shift+E)">
            <Gauge size={13} /> Explain
          </Button>
          <Button onClick={() => explain(true)} disabled={run.running} title="EXPLAIN ANALYZE — executes the query read-only and rolls back">
            Analyze
          </Button>
          <Button variant="ghost" onClick={formatQuery} title="Format SQL">
            <Sparkles size={13} /> Format
          </Button>
          <Button variant="ghost" onClick={() => setSaving({ name: tab.title })} title="Save query (⌘/Ctrl+S)">
            <Save size={13} /> {tab.savedId ? "Save" : "Save as…"}
          </Button>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              onClick={() => {
                if (readOnly && !confirm("Enable write mode? Statements will be COMMITTED to the database.")) return;
                setReadOnly(!readOnly);
              }}
              className={clsx(
                "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-sm font-medium",
                readOnly ? "border-line-strong text-ink-2 hover:bg-surface-hover" : "border-critical bg-critical text-white",
              )}
              title={readOnly ? "Read-only: queries run in a read-only transaction that is rolled back" : "Write mode: changes are committed"}
            >
              {readOnly ? <Lock size={13} /> : <Unlock size={13} />}
              {readOnly ? "Read-only" : "Write mode"}
            </button>
            <Select
              label="Rows"
              value={String(maxRows)}
              onChange={(v) => setMaxRows(Number(v))}
              options={[100, 500, 1000, 5000, 10000, 50000].map((n) => ({ value: String(n), label: formatNumber(n) }))}
            />
            <Select
              label="Timeout"
              value={String(timeout)}
              onChange={(v) => setTimeoutSeconds(Number(v))}
              options={[10, 30, 60, 300, 900].map((n) => ({ value: String(n), label: n < 60 ? `${n}s` : `${n / 60}m` }))}
            />
          </div>
        </div>

        {!readOnly && (
          <div className="flex items-center gap-2 border-b border-critical/30 bg-critical-soft px-3 py-1.5 text-xs text-ink">
            <AlertTriangle size={13} className="text-critical" />
            Write mode: statements run outside the read-only guard and are committed. Multiple statements are allowed.
          </div>
        )}

        {/* Editor */}
        <div style={{ height: editorHeight }} className="shrink-0 overflow-hidden">
          <SqlEditor
            value={tab.sql}
            onChange={(sql) => updateTab(tab.id, { sql })}
            schema={schema.data ?? []}
            onRun={execute}
            onExplain={() => explain(false)}
            onSave={() => (tab.savedId ? save(tab.title) : setSaving({ name: tab.title }))}
            onReady={(view) => (viewRef.current = view)}
          />
        </div>
        <div
          onPointerDown={startResize}
          role="separator"
          aria-orientation="horizontal"
          className="h-1.5 shrink-0 cursor-row-resize border-y border-line bg-surface-2 hover:bg-accent-soft"
        />

        {/* Results */}
        <div className="flex min-h-0 flex-1 flex-col bg-surface">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-3 py-1.5">
            <Segmented
              size="sm"
              value={run.view}
              onChange={(view) => updateRun(tab.id, { view })}
              options={[
                { value: "results", label: "Results" },
                { value: "chart", label: "Chart" },
                { value: "plan", label: "Plan" },
                { value: "messages", label: "Messages" },
              ]}
            />
            {result && !run.error && (
              <span className="tabular text-xs text-ink-2">
                {result.columns.length
                  ? `${formatNumber(result.row_count)} row${result.row_count === 1 ? "" : "s"}${result.truncated ? ` (limited to ${formatNumber(maxRows)} — more available)` : ""}`
                  : result.status_message}
                {" · "}
                {formatDuration(result.duration_ms)}
              </span>
            )}
            {result && result.columns.length > 0 && (
              <div className="ml-auto flex items-center gap-1">
                <Button size="sm" variant="ghost" onClick={() => copyResults("tsv")} title="Copy as tab-separated (paste into a spreadsheet)">
                  <ClipboardCopy size={13} /> Copy
                </Button>
                <Button size="sm" variant="ghost" onClick={() => copyResults("json")} title="Copy as JSON">
                  <FileJson size={13} /> JSON
                </Button>
                <Button size="sm" variant="ghost" onClick={exportCsv} title="Download the complete result (no row limit) as CSV">
                  <Download size={13} /> Export CSV
                </Button>
              </div>
            )}
          </div>

          <div className="min-h-0 flex-1">
            {run.error && (run.view === "results" || run.view === "chart") ? (
              <QueryErrorView error={run.error} />
            ) : run.view === "results" ? (
              result?.columns.length ? (
                <ResultsGrid key={result.query_id} columns={result.columns} rows={result.rows} />
              ) : run.running ? (
                <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
                  <Spinner /> Running…
                </div>
              ) : result ? (
                <Empty>{result.status_message ?? "Done"}{result.affected_rows != null && ` — ${formatNumber(result.affected_rows)} rows affected`}</Empty>
              ) : (
                <Empty>Run a query to see results here</Empty>
              )
            ) : run.view === "chart" ? (
              result?.columns.length ? <ResultChart key={result.query_id} columns={result.columns} rows={result.rows} /> : <Empty>Run a query that returns rows to chart it</Empty>
            ) : run.view === "plan" ? (
              run.plan ? (
                <pre className="h-full overflow-auto p-3 font-mono text-xs leading-relaxed">{run.plan}</pre>
              ) : (
                <Empty>Click Explain or Analyze to see the query plan</Empty>
              )
            ) : (
              <MessagesView run={run} />
            )}
          </div>
        </div>
      </div>

      {saving && (
        <div className="fixed inset-0 z-[1300] grid place-items-center bg-black/30 p-4" role="dialog" aria-modal aria-label="Save query">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save(saving.name).catch((error) => alert(error.message));
            }}
            className="w-full max-w-sm rounded-xl border border-line bg-surface p-4 shadow-card"
          >
            <h2 className="text-sm font-semibold">{tab.savedId ? "Update saved query" : "Save query"}</h2>
            <input
              autoFocus
              value={saving.name}
              onChange={(e) => setSaving({ name: e.target.value })}
              placeholder="Query name"
              className="mt-3 h-9 w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm outline-none focus:border-accent"
            />
            <div className="mt-4 flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setSaving(null)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" disabled={!saving.name.trim()}>
                Save
              </Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(since);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, []);
  return <span className="tabular text-xs opacity-80">{formatDuration(Math.max(0, now - since))}</span>;
}

function QueryErrorView({ error }: { error: QueryError }) {
  return (
    <div className="h-full overflow-auto p-4">
      <div className="rounded-lg border border-critical/30 bg-critical-soft p-3 text-sm">
        <div className="flex items-center gap-2 font-semibold text-critical">
          {error.status === "cancelled" ? <Ban size={15} /> : <AlertTriangle size={15} />}
          {error.status === "cancelled" ? "Query cancelled" : "Query failed"}
          {error.sqlstate && <span className="font-mono text-xs font-normal text-muted">SQLSTATE {error.sqlstate}</span>}
        </div>
        <p className="mt-1.5 whitespace-pre-wrap font-mono text-xs text-ink">{error.message}</p>
        {error.detail && <p className="mt-1.5 text-xs text-ink-2">Detail: {error.detail}</p>}
        {error.hint && <p className="mt-1 text-xs text-ink-2">Hint: {error.hint}</p>}
        {error.position && <p className="mt-1 text-xs text-muted">At character {error.position} — highlighted in the editor.</p>}
      </div>
    </div>
  );
}

function MessagesView({ run }: { run: Run }) {
  const result = run.result;
  if (!result && !run.error) return <Empty>No messages yet</Empty>;
  const notices = result?.notices ?? run.error?.notices ?? [];

  return (
    <div className="h-full overflow-auto p-3 font-mono text-xs leading-relaxed">
      {result && (
        <>
          <div>Statement: {result.statement}</div>
          <div>Status: {result.status_message}</div>
          {result.affected_rows != null && <div>Rows affected: {formatNumber(result.affected_rows)}</div>}
          {result.columns.length > 0 && <div>Rows returned: {formatNumber(result.row_count)}{result.truncated && " (truncated)"}</div>}
          <div>Duration: {formatDuration(result.duration_ms)}</div>
        </>
      )}
      {run.error && <div className="text-critical">Error: {run.error.message}</div>}
      {notices.map((notice, i) => (
        <div key={i} className="text-ink-2">
          NOTICE: {notice}
        </div>
      ))}
    </div>
  );
}
