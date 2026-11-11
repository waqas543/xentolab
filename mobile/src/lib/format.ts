/** 245366 -> "245k", 1234 -> "1.2k", 950 -> "950" */
export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return '–';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, '')}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1).replace(/\.0$/, '')}k`;
  return String(n);
}

export function count(n: number | null | undefined): string {
  return n === null || n === undefined ? '–' : n.toLocaleString('en-US');
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${count(n)} ${n === 1 ? one : many}`;
}

export function distance(km: number | undefined): string | null {
  if (km === undefined || km === null) return null;
  if (km < 1) return `${Math.max(10, Math.round((km * 1000) / 10) * 10)} m`;
  return `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
}

export function hostname(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.replace(/^https?:\/\//i, '').replace(/^www\./i, '').replace(/\/.*$/, '');
}

export function placeLine(place: { area: string | null; city: string | null }): string {
  return [place.area, place.city].filter(Boolean).join(', ');
}

export function monthYear(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
}

/** "1 place", "2.3k places" */
export function places(n: number): string {
  return `${compact(n)} ${n === 1 ? 'place' : 'places'}`;
}

/** 42.2 -> "42%", 3.8 -> "3.8%", null -> "–" */
export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '–';
  return `${value >= 10 || Number.isInteger(value) ? Math.round(value) : value.toFixed(1)}%`;
}

/** 4.37 -> "4.4" */
export function stars(value: number | null | undefined): string {
  return value === null || value === undefined ? '–' : value.toFixed(1);
}

/** 1.77 -> "1.8× usual" */
export function times(index: number | null | undefined): string {
  if (index === null || index === undefined) return '–';
  return `${index >= 10 ? Math.round(index) : index.toFixed(1)}× usual`;
}

/** "1st", "2nd", "23rd" */
export function ordinal(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${suffix}`;
}

const UNCOUNTABLE = /(ing|ware|ics|estate|media|education|agriculture|tuition|signage|insurance|tax|trade|business|care|aid|food|equipment)$/;

/**
 * A category label as a plural noun for sentences: "Pharmacy" -> "pharmacies", "Fuel station" -> "fuel stations",
 * "IT & software" -> "IT & software businesses". Acronyms keep their case.
 */
export function categoryNoun(label: string | null | undefined, many = true): string {
  if (!label) return many ? 'places' : 'place';
  const lower = label.split(' ').map((w) => (/^[A-Z]{2,}$/.test(w) ? w : w.toLowerCase())).join(' ');
  if (!many) return lower;
  if (/ & | and |\//.test(lower) || UNCOUNTABLE.test(lower) || lower.endsWith('s')) return `${lower} businesses`;
  if (/[^aeiou]y$/.test(lower)) return `${lower.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/.test(lower)) return `${lower}es`;
  return `${lower}s`;
}
