/** Small presentation-only formatters shared across pages. */

/** Render an uptime in seconds as a compact `1d 2h`, `2h 14m`, or `47s`. */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const d = Math.floor(seconds / 86_400)
  const h = Math.floor((seconds % 86_400) / 3_600)
  const m = Math.floor((seconds % 3_600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m`
  return `${Math.floor(seconds)}s`
}

// Thresholds → the Intl relative-time unit to render at that magnitude.
const REL_UNITS: [limit: number, secs: number, unit: Intl.RelativeTimeFormatUnit][] = [
  [60, 1, 'second'],
  [3_600, 60, 'minute'],
  [86_400, 3_600, 'hour'],
  [2_592_000, 86_400, 'day'],
]

/** Render an ISO timestamp as a coarse, localized relative age (`3 min ago` / `há 3 min`). */
export function timeAgo(iso: string): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return '—'
  const diff = Math.max(0, (Date.now() - then) / 1_000)
  for (const [limit, secs, unit] of REL_UNITS) {
    if (diff < limit) return relFmt.format(-Math.floor(diff / secs), unit)
  }
  return relFmt.format(-Math.floor(diff / 2_592_000), 'month')
}

const DATE_FMT_OPTS: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
}
// Both rebuilt on language change (see `i18n/index.ts`) so calendar dates and
// relative ages track the active language; start on the runtime default until
// i18n boots.
let dateFmt = new Intl.DateTimeFormat(undefined, DATE_FMT_OPTS)
let relFmt = new Intl.RelativeTimeFormat(undefined, { numeric: 'always', style: 'narrow' })

/** Point the shared date + relative-time formatters at a new locale (e.g. `pt-BR`). */
export function setDateLocale(locale: string): void {
  dateFmt = new Intl.DateTimeFormat(locale, DATE_FMT_OPTS)
  relFmt = new Intl.RelativeTimeFormat(locale, { numeric: 'always', style: 'narrow' })
}

/** Render an ISO timestamp as an absolute calendar date (`Jun 12, 2026`). */
export function formatDate(iso: string): string {
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return '—'
  return dateFmt.format(ms)
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB']

/** Render a byte count as a compact human size (`1.2 MB`, `456 KB`). */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(1)} ${BYTE_UNITS[unit]}`
}
