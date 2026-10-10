const integer = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function formatNumber(value: number | null | undefined) {
  return value == null ? "–" : integer.format(value);
}

export function formatCompact(value: number | null | undefined) {
  return value == null ? "–" : compact.format(value);
}

export function formatPercent(part: number, whole: number, digits = 1) {
  if (!whole) return "–";
  return `${((part / whole) * 100).toFixed(digits)}%`;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "–";
  return new Date(value).toLocaleDateString("en-GB", { year: "numeric", month: "short", day: "numeric" });
}

export function formatDuration(ms: number) {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatBytes(bytes: number) {
  const units = ["B", "kB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

/** Turn an OSM value like "fast_food" into "Fast food". */
export function humanize(value: string | number | null | undefined) {
  if (value == null) return "–";
  const text = String(value).replace(/[_;]/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function osmUrl(type: string, id: number) {
  const kind = type === "n" ? "node" : type === "w" ? "way" : "relation";
  return `https://www.openstreetmap.org/${kind}/${id}`;
}

export function websiteUrl(value: string) {
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

export function formatSeconds(seconds: number | null | undefined) {
  if (seconds == null) return "–";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
}

export function formatClock(seconds: number | null | undefined) {
  if (seconds == null) return "–";
  const s = Math.max(0, Math.round(seconds));
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((v) => String(v).padStart(2, "0")).join(":");
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return "–";
  return new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function timeAgo(value: string | null | undefined) {
  if (!value) return "–";
  const seconds = (Date.now() - new Date(value).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}
