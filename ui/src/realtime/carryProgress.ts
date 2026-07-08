/** The progress counts an ingestion event carries. */
export interface ProgressCounts {
  current: number | null
  total: number | null
  pct: number | null
}

/**
 * Carry forward the last-known progress counts. A paused event (e.g. `rate_limited`)
 * may omit `current`/`total`/`pct`; keep the previously stored values so the row still
 * shows where it stopped (e.g. 128/1436) instead of blanking. Shared by
 * {@link useIngestionProgress} and {@link useIngestionQueue} so the rule lives in one
 * place.
 */
export function carryProgress(
  msg: ProgressCounts,
  existing: ProgressCounts | undefined,
): ProgressCounts {
  return {
    current: msg.current ?? existing?.current ?? null,
    total: msg.total ?? existing?.total ?? null,
    pct: msg.pct ?? existing?.pct ?? null,
  }
}
