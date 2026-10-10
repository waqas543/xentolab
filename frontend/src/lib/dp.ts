/** Data platform API client and types. */

import { API_URL, ApiError, saveBlob } from "./api";

const KEY_STORAGE = "dp-api-key";

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setApiKey(key: string | null) {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {}
}

function headers(json = true): HeadersInit {
  const key = getApiKey();
  return { ...(json ? { "content-type": "application/json" } : {}), ...(key ? { "x-api-key": key } : {}) };
}

export async function dp<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/data-platform${path}`, { ...init, headers: { ...headers(), ...init?.headers } });
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the backend running?`, null);
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail = (body as { detail?: unknown })?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d?.msg ?? String(d)).join("; ")
          : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, message, body);
  }
  return body as T;
}

export const dpPost = <T,>(path: string, data: unknown) => dp<T>(path, { method: "POST", body: JSON.stringify(data) });
export const dpPatch = <T,>(path: string, data: unknown) => dp<T>(path, { method: "PATCH", body: JSON.stringify(data) });

export async function dpDownload(path: string, data: unknown, filename: string) {
  const response = await fetch(`${API_URL}/api/data-platform${path}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new ApiError(response.status, (await response.json().catch(() => null))?.detail ?? "Export failed", null);
  saveBlob(await response.blob(), filename);
}

export function eventsUrl(runId: number) {
  return `${API_URL}/api/data-platform/runs/${runId}/events`;
}

// ---------------------------------------------------------------- types

export type RunStatus =
  | "PENDING" | "QUEUED" | "DISCOVERING" | "DOWNLOADING" | "VALIDATING" | "BRONZE_LOADING" | "SILVER_PROCESSING"
  | "ENTITY_RESOLUTION" | "GOLD_PROCESSING" | "COMPLETED" | "FAILED" | "CANCELLED" | "PARTIALLY_COMPLETED";
export type StageStatus = "PENDING" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "SKIPPED" | "FAILED" | "CANCELLED";

export interface Run {
  id: number;
  pipeline_definition_id: string;
  source_id: string;
  status: RunStatus;
  trigger_type: string;
  triggered_by: string | null;
  parameters: Record<string, unknown>;
  country: string | null;
  dataset: string | null;
  current_stage: string | null;
  records_discovered: number;
  bronze_records: number;
  silver_records: number;
  gold_records: number;
  bytes_downloaded: number;
  error_count: number;
  warning_count: number;
  error_summary: string | null;
  versions: Record<string, string>;
  outputs: Record<string, unknown>;
  retry_of_run_id: number | null;
  cancel_requested_at: string | null;
  cancel_requested_by: string | null;
  worker_id: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  duration_seconds: number | null;
  is_terminal: boolean;
}

export interface StageRun {
  id: number;
  stage: string;
  position: number;
  status: StageStatus;
  attempt: number;
  max_attempts: number;
  started_at: string | null;
  finished_at: string | null;
  next_retry_at: string | null;
  records_in: number | null;
  records_out: number | null;
  records_rejected: number | null;
  bytes_processed: number | null;
  progress_current: number | null;
  progress_total: number | null;
  progress_unit: string | null;
  progress_message: string | null;
  progress_percent: number | null;
  metrics: Record<string, unknown>;
  skip_reason: string | null;
  error_message: string | null;
  duration_seconds: number | null;
}

export interface RunDetail {
  run: Run;
  stages: StageRun[];
  errors?: PipelineError[];
  quality?: QualityResult[];
  artifact?: Artifact | null;
}

export interface PipelineError {
  id: number;
  stage: string | null;
  attempt: number | null;
  exception_type: string;
  message: string;
  retryable: boolean;
  context: Record<string, unknown>;
  traceback: string | null;
  created_at: string;
}

export interface QualityResult {
  id: number;
  stage: string;
  dataset_id: string;
  check_name: string;
  check_type: string;
  severity: "error" | "warn";
  status: "passed" | "warned" | "failed" | "error";
  records_checked: number | null;
  records_failed: number | null;
  failure_percentage: number | null;
  threshold_percentage: number | null;
  details: Record<string, unknown>;
}

export interface Artifact {
  id: number;
  source_id: string;
  dataset_name: string;
  country: string | null;
  filename: string;
  size_bytes: number | null;
  sha256: string | null;
  source_version: string | null;
  source_release_date: string | null;
  status: string;
  storage_key: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface JsonSchemaProperty {
  type: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  pattern?: string;
}

export interface PipelineDefinition {
  id: string;
  source_id: string;
  name: string;
  description: string | null;
  parameter_schema: { properties: Record<string, JsonSchemaProperty>; required?: string[] };
  default_parameters: Record<string, unknown>;
  stages: string[];
  enabled: boolean;
  quality_overrides: Record<string, unknown>;
}

export interface Source {
  id: string;
  name: string;
  description: string | null;
  homepage: string | null;
  license: string | null;
  enabled: boolean;
  registered: boolean;
  capabilities: Record<string, boolean | string[]>;
  /** Credentials the adapter needs (environment variable names) and whether the worker has them. Never values. */
  credentials: { name: string; configured: boolean }[];
  status: RunStatus | null;
  last_run: { id: number; status: RunStatus; created_at: string } | null;
  last_success: { id: number; finished_at: string } | null;
  last_failure: { id: number; finished_at: string; error_summary: string | null } | null;
  latest_artifact: { id: number; filename: string; source_version: string | null; size_bytes: number; created_at: string } | null;
  storage_bytes: number;
  records: Record<string, number>;
  schedules: { id: number; name: string; cadence: string; cron: string | null; next_run_at: string | null }[];
  pipelines: PipelineDefinition[];
}

export interface FilterField {
  field: string;
  label: string;
  type: "text" | "multi_select" | "range" | "boolean" | "date" | "datetime" | "geo";
  data_type: string;
  operators: string[];
  sortable: boolean;
  searchable: boolean;
  visible: boolean;
  facet: boolean;
  description: string | null;
}

export interface Condition {
  field: string;
  operator: string;
  value?: unknown;
}

export interface FilterGroup {
  operator: "and" | "or";
  conditions: (Condition | FilterGroup)[];
}

export interface DatasetInfo {
  id: string;
  layer: "bronze" | "silver" | "gold" | "er";
  label: string;
  description: string;
  engine: string;
  accessible: boolean;
  row_count: number | null;
  size_bytes: number | null;
  partitions: number | null;
  last_committed_at: string | null;
}

export interface QueryResult {
  columns: string[];
  rows: unknown[][];
  total: number;
  page: number;
  page_size: number;
}

export const isGroup = (item: Condition | FilterGroup): item is FilterGroup => "conditions" in item;

export function emptyGroup(): FilterGroup {
  return { operator: "and", conditions: [] };
}

/** Encode/decode a filter group for URLs (drill-down links). */
export function encodeFilters(group: FilterGroup | null): string {
  return group && group.conditions.length ? JSON.stringify(group) : "";
}

export function decodeFilters(raw: string | null): FilterGroup {
  if (!raw) return emptyGroup();
  try {
    const parsed = JSON.parse(raw);
    return parsed && Array.isArray(parsed.conditions) ? parsed : emptyGroup();
  } catch {
    return emptyGroup();
  }
}

export function explorerHref(dataset: string, filters: FilterGroup, extra?: Condition[]) {
  const merged: FilterGroup = { operator: "and", conditions: [...(filters.operator === "and" ? filters.conditions : [filters]), ...(extra ?? [])] };
  const params = new URLSearchParams({ dataset });
  const encoded = encodeFilters(merged);
  if (encoded) params.set("f", encoded);
  return `/data-platform/explorer?${params}`;
}

export const STAGE_LABELS: Record<string, string> = {
  DISCOVER: "Discover",
  DOWNLOAD: "Download",
  VALIDATE: "Validate",
  BRONZE_INGEST: "Bronze ingest",
  BRONZE_QUALITY: "Bronze quality",
  SILVER_TRANSFORM: "Silver transform",
  SILVER_QUALITY: "Silver quality",
  ENTITY_RESOLUTION: "Entity resolution",
  GOLD_BUILD: "Gold build",
  GOLD_QUALITY: "Gold quality",
  ANALYTICS_REFRESH: "Analytics refresh",
};
