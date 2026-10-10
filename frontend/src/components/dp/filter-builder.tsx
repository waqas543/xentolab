"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Plus, X } from "lucide-react";
import { useId, useState } from "react";
import { dpPost, isGroup, type Condition, type FilterField, type FilterGroup } from "@/lib/dp";
import { formatNumber, humanize } from "@/lib/format";
import { useDebounced } from "@/lib/hooks";

export const OPERATOR_LABELS: Record<string, string> = {
  equals: "is",
  not_equals: "is not",
  contains: "contains",
  starts_with: "starts with",
  in: "is any of",
  not_in: "is none of",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  between: "between",
  is_null: "is empty",
  is_not_null: "is not empty",
  has_key: "has tag",
  within_bbox: "within box",
  within_radius: "within radius",
};

const NO_VALUE = new Set(["is_null", "is_not_null"]);
const MULTI = new Set(["in", "not_in"]);

function defaultOperator(field: FilterField) {
  if (field.type === "boolean") return "equals";
  if (field.type === "multi_select") return "in";
  if (field.type === "geo") return "within_radius";
  if (field.data_type === "map") return "has_key";
  if (field.type === "text") return "contains";
  return field.operators[0];
}

function defaultValue(field: FilterField, operator: string): unknown {
  if (NO_VALUE.has(operator)) return undefined;
  if (MULTI.has(operator)) return [];
  if (operator === "between") return ["", ""];
  if (field.type === "boolean") return true;
  if (operator === "within_radius") return { lat: "", lon: "", km: 5 };
  if (operator === "within_bbox") return ["", "", "", ""];
  return "";
}

/** True when a condition has the value its operator needs (incomplete ones are not sent). */
export function isComplete(condition: Condition | FilterGroup): boolean {
  if (isGroup(condition)) return condition.conditions.some(isComplete);
  const { operator, value } = condition;
  if (NO_VALUE.has(operator)) return true;
  if (Array.isArray(value)) return value.length > 0 && value.every((v) => v !== "" && v != null);
  if (value && typeof value === "object") return Object.values(value).every((v) => v !== "" && v != null);
  return value !== "" && value != null;
}

export function pruneIncomplete(group: FilterGroup): FilterGroup {
  return {
    operator: group.operator,
    conditions: group.conditions
      .map((c) => (isGroup(c) ? pruneIncomplete(c) : c))
      .filter((c) => (isGroup(c) ? c.conditions.length > 0 : isComplete(c))),
  };
}

export function FilterBuilder({
  dataset,
  fields,
  value,
  onChange,
  depth = 0,
}: {
  dataset: string;
  fields: FilterField[];
  value: FilterGroup;
  onChange: (group: FilterGroup) => void;
  depth?: number;
}) {
  const filterable = fields.filter((f) => f.operators.length);
  const update = (index: number, item: Condition | FilterGroup) =>
    onChange({ ...value, conditions: value.conditions.map((c, i) => (i === index ? item : c)) });
  const remove = (index: number) => onChange({ ...value, conditions: value.conditions.filter((_, i) => i !== index) });
  const addCondition = () => {
    const field = filterable[0];
    const operator = defaultOperator(field);
    onChange({ ...value, conditions: [...value.conditions, { field: field.field, operator, value: defaultValue(field, operator) }] });
  };

  return (
    <div className={clsx("flex flex-col gap-1.5", depth > 0 && "rounded-lg border border-line bg-surface-2/60 p-2")}>
      {value.conditions.length > 1 && (
        <div className="flex items-center gap-1.5 text-xs text-ink-2">
          Match
          <select
            value={value.operator}
            onChange={(e) => onChange({ ...value, operator: e.target.value as "and" | "or" })}
            className="h-6 rounded border border-line-strong bg-surface px-1 text-xs"
          >
            <option value="and">all</option>
            <option value="or">any</option>
          </select>
          of these conditions
        </div>
      )}
      {value.conditions.map((condition, index) =>
        isGroup(condition) ? (
          <div key={index} className="flex items-start gap-1">
            <div className="min-w-0 flex-1">
              <FilterBuilder dataset={dataset} fields={fields} value={condition} onChange={(g) => update(index, g)} depth={depth + 1} />
            </div>
            <RemoveButton onClick={() => remove(index)} />
          </div>
        ) : (
          <ConditionRow
            key={index}
            dataset={dataset}
            fields={filterable}
            condition={condition}
            onChange={(c) => update(index, c)}
            onRemove={() => remove(index)}
          />
        ),
      )}
      <div className="flex gap-1">
        <button onClick={addCondition} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-accent-ink hover:bg-surface-hover">
          <Plus size={12} /> Condition
        </button>
        {depth < 2 && (
          <button
            onClick={() => onChange({ ...value, conditions: [...value.conditions, { operator: value.operator === "and" ? "or" : "and", conditions: [] }] })}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-2 hover:bg-surface-hover"
          >
            <Plus size={12} /> Group
          </button>
        )}
      </div>
    </div>
  );
}

function RemoveButton({ onClick }: { onClick: () => void }) {
  return (
    <button aria-label="Remove condition" onClick={onClick} className="mt-1 rounded p-1 text-muted hover:bg-surface-hover hover:text-ink">
      <X size={14} />
    </button>
  );
}

const control = "h-8 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus:border-accent";

function ConditionRow({
  dataset,
  fields,
  condition,
  onChange,
  onRemove,
}: {
  dataset: string;
  fields: FilterField[];
  condition: Condition;
  onChange: (c: Condition) => void;
  onRemove: () => void;
}) {
  const field = fields.find((f) => f.field === condition.field) ?? fields[0];
  return (
    <div className="flex flex-wrap items-start gap-1.5">
      <select
        aria-label="Field"
        value={field.field}
        onChange={(e) => {
          const next = fields.find((f) => f.field === e.target.value)!;
          const operator = defaultOperator(next);
          onChange({ field: next.field, operator, value: defaultValue(next, operator) });
        }}
        className={clsx(control, "max-w-44")}
      >
        {fields.map((f) => (
          <option key={f.field} value={f.field}>
            {f.label}
          </option>
        ))}
      </select>
      <select
        aria-label="Operator"
        value={condition.operator}
        onChange={(e) => onChange({ ...condition, operator: e.target.value, value: defaultValue(field, e.target.value) })}
        className={clsx(control, "max-w-36")}
      >
        {field.operators.map((op) => (
          <option key={op} value={op}>
            {OPERATOR_LABELS[op] ?? op}
          </option>
        ))}
      </select>
      <div className="min-w-0 flex-1">
        <ValueEditor dataset={dataset} field={field} condition={condition} onChange={(value) => onChange({ ...condition, value })} />
      </div>
      <RemoveButton onClick={onRemove} />
    </div>
  );
}

function ValueEditor({
  dataset,
  field,
  condition,
  onChange,
}: {
  dataset: string;
  field: FilterField;
  condition: Condition;
  onChange: (value: unknown) => void;
}) {
  const { operator, value } = condition;
  if (NO_VALUE.has(operator)) return null;

  if (operator === "within_radius") {
    const v = (value ?? {}) as Record<string, unknown>;
    return (
      <div className="flex gap-1">
        {(["lat", "lon", "km"] as const).map((key) => (
          <input key={key} type="number" step="any" placeholder={key} value={String(v[key] ?? "")}
            onChange={(e) => onChange({ ...v, [key]: e.target.value === "" ? "" : Number(e.target.value) })} className={clsx(control, "w-24")} />
        ))}
      </div>
    );
  }
  if (operator === "within_bbox") {
    const v = (Array.isArray(value) ? value : ["", "", "", ""]) as unknown[];
    return (
      <div className="flex gap-1">
        {["west", "south", "east", "north"].map((label, i) => (
          <input key={label} type="number" step="any" placeholder={label} value={String(v[i] ?? "")}
            onChange={(e) => onChange(v.map((x, j) => (j === i ? (e.target.value === "" ? "" : Number(e.target.value)) : x)))}
            className={clsx(control, "w-20")} />
        ))}
      </div>
    );
  }
  if (field.type === "boolean") {
    return (
      <select value={String(value)} onChange={(e) => onChange(e.target.value === "true")} className={control}>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  }
  const inputType = field.type === "range" ? "number" : field.type === "date" ? "date" : field.type === "datetime" ? "datetime-local" : "text";
  if (operator === "between") {
    const v = (Array.isArray(value) ? value : ["", ""]) as unknown[];
    return (
      <div className="flex items-center gap-1">
        {[0, 1].map((i) => (
          <input key={i} type={inputType} value={String(v[i] ?? "")}
            onChange={(e) => onChange(v.map((x, j) => (j === i ? (inputType === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value) : x)))}
            className={clsx(control, "w-32")} />
        ))}
      </div>
    );
  }
  if (field.facet && field.data_type !== "map" && operator !== "contains" && operator !== "starts_with") {
    return <FacetPicker dataset={dataset} field={field} multiple={MULTI.has(operator)} value={value} onChange={onChange} />;
  }
  if (MULTI.has(operator)) {
    const list = Array.isArray(value) ? value : [];
    return (
      <input placeholder="comma-separated values" defaultValue={list.join(", ")}
        onBlur={(e) => onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean).map((s) => (inputType === "number" ? Number(s) : s)))}
        className={clsx(control, "w-full")} />
    );
  }
  return (
    <input type={inputType} value={String(value ?? "")} placeholder={field.data_type === "map" ? "tag key, e.g. shop" : "value"}
      onChange={(e) => onChange(inputType === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value)}
      className={clsx(control, "w-full")} />
  );
}

export function FacetPicker({
  dataset,
  field,
  multiple,
  value,
  onChange,
}: {
  dataset: string;
  field: FilterField;
  multiple: boolean;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const debounced = useDebounced(search, 250);
  const listId = useId();
  const selected = multiple ? ((Array.isArray(value) ? value : []) as string[]) : value ? [String(value)] : [];
  const { data, isFetching } = useQuery({
    queryKey: ["facet", dataset, field.field, debounced],
    queryFn: () => dpPost<{ value: string; count: number }[]>(`/datasets/${dataset}/values/${field.field}`, { search: debounced || null, limit: 30 }),
    enabled: open,
    staleTime: 60_000,
  });
  const label = (v: string) => (field.field === "category" ? humanize(v) : v);

  const choose = (v: string) => {
    if (multiple) onChange(selected.includes(v) ? selected.filter((s) => s !== v) : [...selected, v]);
    else {
      onChange(v);
      setOpen(false);
    }
  };

  return (
    <div className="relative" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setOpen(false)}>
      <div className={clsx(control, "flex h-auto min-h-8 flex-wrap items-center gap-1 py-1")} onClick={() => setOpen(true)}>
        {selected.map((v) => (
          <span key={v} className="inline-flex items-center gap-0.5 rounded bg-accent-soft/60 px-1.5 text-xs">
            {label(v)}
            <button aria-label={`Remove ${v}`} onClick={(e) => { e.stopPropagation(); choose(v); }} className="text-muted hover:text-ink">
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onFocus={() => setOpen(true)}
          placeholder={selected.length ? "" : "Search values…"}
          aria-controls={listId}
          className="min-w-20 flex-1 bg-transparent text-sm outline-none"
        />
      </div>
      {open && (
        <ul id={listId} tabIndex={-1} className="absolute left-0 right-0 z-30 mt-1 max-h-64 overflow-auto rounded-md border border-line bg-surface py-1 shadow-card">
          {isFetching && !data && <li className="px-2 py-1 text-xs text-muted">Loading…</li>}
          {data?.map((option) => (
            <li key={String(option.value)}>
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(String(option.value))}
                className={clsx("flex w-full items-center justify-between gap-2 px-2 py-1 text-left text-sm hover:bg-surface-hover",
                  selected.includes(String(option.value)) && "font-medium text-accent-ink")}
              >
                <span className="truncate" dir="auto">{label(String(option.value))}</span>
                <span className="tabular text-xs text-muted">{formatNumber(option.count)}</span>
              </button>
            </li>
          ))}
          {data && !data.length && <li className="px-2 py-1 text-xs text-muted">No values</li>}
        </ul>
      )}
    </div>
  );
}
