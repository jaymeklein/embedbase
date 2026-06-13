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

const UNITS: [limit: number, secs: number, label: string][] = [
  [60, 1, 's'],
  [3_600, 60, 'm'],
  [86_400, 3_600, 'h'],
  [2_592_000, 86_400, 'd'],
]

/** Render an ISO timestamp as a coarse relative age (`3m ago`, `2h ago`). */
export function timeAgo(iso: string): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return '—'
  const diff = Math.max(0, (Date.now() - then) / 1_000)
  for (const [limit, secs, label] of UNITS) {
    if (diff < limit) return `${Math.floor(diff / secs)}${label} ago`
  }
  return `${Math.floor(diff / 2_592_000)}mo ago`
}

const DATE_FMT = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

/** Render an ISO timestamp as an absolute calendar date (`Jun 12, 2026`). */
export function formatDate(iso: string): string {
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return '—'
  return DATE_FMT.format(ms)
}
