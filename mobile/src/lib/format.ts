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
