"use client";

import type { JsonSchemaProperty, PipelineDefinition } from "@/lib/dp";

/** Renders pipeline parameters from the pipeline's JSON schema (adapters define them; no per-source UI code). */
export function ParamForm({
  definition,
  values,
  onChange,
}: {
  definition: PipelineDefinition;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}) {
  const required = new Set(definition.parameter_schema.required ?? []);
  const entries = Object.entries(definition.parameter_schema.properties);
  const set = (key: string, value: unknown) => onChange({ ...values, [key]: value });

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {entries.map(([key, spec]) => (
        <Field key={key} name={key} spec={spec} required={required.has(key)} value={values[key]} onChange={(v) => set(key, v)} />
      ))}
    </div>
  );
}

function Field({
  name,
  spec,
  required,
  value,
  onChange,
}: {
  name: string;
  spec: JsonSchemaProperty;
  required: boolean;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const types = Array.isArray(spec.type) ? spec.type : [spec.type];
  const nullable = types.includes("null");
  const base = types.find((t) => t !== "null") ?? "string";
  const label = (
    <span className="text-xs font-medium text-ink-2">
      {spec.title ?? name}
      {required && <span className="text-critical"> *</span>}
    </span>
  );
  const input = "h-8 w-full rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus:border-accent";

  if (base === "boolean") {
    return (
      <label className="flex items-start gap-2 rounded-md border border-line p-2">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 accent-[var(--accent)]" />
        <span>
          {label}
          {spec.description && <span className="block text-[11px] text-muted">{spec.description}</span>}
        </span>
      </label>
    );
  }
  return (
    <label className="flex flex-col gap-1">
      {label}
      {spec.enum ? (
        <select value={String(value ?? spec.default ?? "")} onChange={(e) => onChange(e.target.value)} className={input}>
          {spec.enum.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={base === "integer" || base === "number" ? "number" : "text"}
          value={value == null ? "" : String(value)}
          pattern={spec.pattern}
          placeholder={nullable ? "optional" : undefined}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") onChange(nullable ? null : "");
            else onChange(base === "integer" ? parseInt(raw, 10) : base === "number" ? Number(raw) : raw);
          }}
          className={input}
        />
      )}
      {spec.description && <span className="text-[11px] text-muted">{spec.description}</span>}
    </label>
  );
}
