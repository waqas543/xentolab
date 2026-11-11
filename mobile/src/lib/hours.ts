/**
 * Opening hours in OpenStreetMap syntax ("24/7", "Mo-Fr 09:00-17:00; Sa 10:00-14:00; Su off") -> a weekly
 * schedule and a live status ("Open now · closes 5 PM"). Covers the forms listings actually use; anything else
 * (public holidays, month ranges, free text) yields null, and the app simply doesn't show a status.
 *
 * The status uses the phone's clock: people look up places in the country they are in.
 */

const DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'] as const;
const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const SHORT_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Minutes after midnight; an interval may end past 24:00 when it runs overnight (18:00-02:00 -> 1080-1560). */
type Interval = [number, number];
export type Week = Interval[][];            // index 0 = Monday

function minutes(text: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(text.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  return h > 24 || min > 59 ? null : h * 60 + min;
}

function days(spec: string): number[] | null {
  const result = new Set<number>();
  for (const part of spec.split(',')) {
    const [from, to] = part.trim().split('-');
    const a = DAYS.indexOf(from as (typeof DAYS)[number]);
    const b = to === undefined ? a : DAYS.indexOf(to as (typeof DAYS)[number]);
    if (a < 0 || b < 0) return null;
    for (let d = a; ; d = (d + 1) % 7) {          // Sa-Mo wraps over the weekend
      result.add(d);
      if (d === b) break;
    }
  }
  return [...result];
}

function intervals(spec: string): Interval[] | null {
  const text = spec.trim().toLowerCase();
  if (text === 'off' || text === 'closed') return [];
  const result: Interval[] = [];
  for (const part of text.split(',')) {
    const [a, b] = part.split('-');
    const start = a === undefined ? null : minutes(a);
    let end = b === undefined ? null : minutes(b);
    if (start === null || end === null) return null;
    if (end <= start) end += 24 * 60;            // overnight
    result.push([start, end]);
  }
  return result;
}

/** The weekly schedule, or null when the text isn't in a form we can read. */
export function parseHours(text: string | null | undefined): Week | null {
  if (!text) return null;
  const raw = text.trim();
  if (raw === '24/7') return DAYS.map(() => [[0, 24 * 60]]);
  const week: Week = DAYS.map(() => []);
  let understood = false;
  for (const rule of raw.split(';')) {
    const r = rule.trim();
    if (!r) continue;
    if (/^PH\b/i.test(r)) continue;               // public holidays: not knowable here
    const match = /^((?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*[-,]\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))*)\s+(.+)$/.exec(r);
    const dayList = match ? days(match[1].replace(/\s/g, '')) : DAYS.map((_, i) => i);
    const spec = match ? match[2] : r;
    const times = spec === '24/7' || spec === '00:00-24:00' ? [[0, 24 * 60] as Interval] : intervals(spec);
    if (!dayList || !times) return null;
    for (const d of dayList) week[d] = times;     // later rules override earlier ones for their days
    understood = true;
  }
  return understood ? week : null;
}

function clock(total: number): string {
  const m = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  const h = Math.floor(m / 60);
  const min = m % 60;
  const suffix = h < 12 ? 'AM' : 'PM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return min === 0 ? `${h12} ${suffix}` : `${h12}:${String(min).padStart(2, '0')} ${suffix}`;
}

export type HoursStatus = { open: boolean; always: boolean; text: string };

/** "Open 24 hours", "Open now · closes 9 PM", "Closed · opens 10 AM", "Closed · opens Mon 9 AM". */
export function hoursStatus(text: string | null | undefined, now: Date = new Date()): HoursStatus | null {
  const week = parseHours(text);
  if (!week) return null;
  if (week.every((d) => d.length === 1 && d[0][0] === 0 && d[0][1] >= 24 * 60)) {
    return { open: true, always: true, text: 'Open 24 hours' };
  }
  const today = (now.getDay() + 6) % 7;          // JS: 0 = Sunday
  const at = now.getHours() * 60 + now.getMinutes();
  const yesterday = (today + 6) % 7;
  // Still inside last night's overnight hours?
  for (const [, end] of week[yesterday]) {
    if (end > 24 * 60 && at < end - 24 * 60) return { open: true, always: false, text: `Open now · closes ${clock(end)}` };
  }
  for (const [start, end] of week[today]) {
    if (at >= start && at < end) {
      return { open: true, always: false, text: end >= 24 * 60 && end - 24 * 60 === 0 ? 'Open now · until midnight' : `Open now · closes ${clock(end)}` };
    }
  }
  for (const [start] of week[today]) {
    if (start > at) return { open: false, always: false, text: `Closed · opens ${clock(start)}` };
  }
  for (let i = 1; i <= 7; i++) {
    const d = (today + i) % 7;
    if (week[d].length) {
      const label = i === 1 ? 'tomorrow' : SHORT_NAMES[d];
      return { open: false, always: false, text: `Closed · opens ${label} ${clock(week[d][0][0])}` };
    }
  }
  return { open: false, always: false, text: 'Closed' };
}

/** One line per day for display: [["Monday", "9 AM – 5 PM"], ["Sunday", "Closed"], ...]. */
export function weekLines(text: string | null | undefined): [string, string][] | null {
  const week = parseHours(text);
  if (!week) return null;
  return week.map((d, i) => [
    DAY_NAMES[i],
    d.length === 0 ? 'Closed'
      : d.some(([s, e]) => s === 0 && e >= 24 * 60) ? 'Open 24 hours'
      : d.map(([s, e]) => `${clock(s)} – ${clock(e)}`).join(', '),
  ]);
}
