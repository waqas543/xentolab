"use client";

import { PostgreSQL, sql } from "@codemirror/lang-sql";
import { Compartment, Prec } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useMemo, useState } from "react";
import type { TableInfo } from "@/lib/query";
import { useResolvedTheme } from "../theme-toggle";

const surfaceTheme = EditorView.theme({
  "&": { backgroundColor: "var(--surface)", color: "var(--ink)" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in oklab, var(--accent) 6%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "color-mix(in oklab, var(--accent) 10%, transparent)" },
  ".cm-tooltip": { backgroundColor: "var(--surface)", border: "1px solid var(--border)" },
});

export function SqlEditor({
  value,
  onChange,
  schema,
  onRun,
  onExplain,
  onSave,
  onReady,
}: {
  value: string;
  onChange: (value: string) => void;
  schema: TableInfo[];
  onRun: () => void;
  onExplain: () => void;
  onSave: () => void;
  onReady: (view: EditorView) => void;
}) {
  const theme = useResolvedTheme();
  const [view, setView] = useState<EditorView | null>(null);
  const [shortcuts] = useState(() => new Compartment());

  const extensions = useMemo(() => {
    // Autocomplete knows "schema.table" and each table's columns.
    const completionSchema: Record<string, string[]> = {};
    for (const table of schema) {
      completionSchema[`${table.schema}.${table.name}`] = table.columns.map((c) => c.name);
    }

    return [
      sql({ dialect: PostgreSQL, schema: completionSchema, upperCaseKeywords: true }),
      surfaceTheme,
      EditorView.lineWrapping,
      shortcuts.of([]),
    ];
  }, [schema, shortcuts]);

  // Swap in the latest handlers without rebuilding the rest of the editor.
  useEffect(() => {
    view?.dispatch({
      effects: shortcuts.reconfigure(
        Prec.highest(
          keymap.of([
            { key: "Mod-Enter", run: () => (onRun(), true) },
            { key: "Mod-Shift-e", run: () => (onExplain(), true) },
            { key: "Mod-s", run: () => (onSave(), true), preventDefault: true },
          ]),
        ),
      ),
    });
  }, [view, shortcuts, onRun, onExplain, onSave, extensions]);

  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme={theme}
      height="100%"
      className="h-full"
      onCreateEditor={(created) => {
        setView(created);
        onReady(created);
      }}
      basicSetup={{ highlightActiveLine: true, foldGutter: false, autocompletion: true }}
    />
  );
}
