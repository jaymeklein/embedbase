/**
 * Live, global ingestion queue over ONE WebSocket (topic `ingestion-queue`, fed by
 * the worker alongside the per-collection `ingestion:{id}` topic). Built on the
 * generic {@link useChannel}.
 *
 * Tracks every in-flight document plus those that finished/failed recently (the
 * backend snapshot replays the last hour on connect). Per file it accumulates the
 * most recent chunk labels — capped, so a 1400-chunk PDF can't grow unbounded.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { qk } from '../api/hooks'
import { carryProgress } from './carryProgress'
import { useChannel } from './useChannel'
import type { IngestPhase } from './useIngestionProgress'

/** Cap on the live per-file chunk list — newest kept, older dropped. */
const RECENT_CHUNKS_CAP = 50

/**
 * Debounce window for the jobs list + stats refresh (below). Two things fire a burst of
 * qualifying events: every (re)connect replays the backend's last-hour snapshot, and a bulk
 * ingest settles many documents in quick succession. Invalidating per event would refetch the
 * whole (large) job list + stats once per event; under a flaky socket those stack faster than
 * they resolve and exhaust the browser's connection pool (net::ERR_INSUFFICIENT_RESOURCES).
 * Coalescing the burst into a single refetch after a brief quiet window keeps the list live
 * (sub-second) while the 5s stats poll (useJobStats) keeps the header counts current meanwhile.
 */
const REFRESH_DEBOUNCE_MS = 400

export interface QueueChunk {
  index: number
  label: string
}

interface QueueEvent {
  document_id: string
  collection_id: string
  collection_name: string
  workspace_id: string | null // for the queue's "open in collection" link
  filename: string
  phase: IngestPhase
  current: number | null
  total: number | null
  pct: number | null
  status: 'processing' | 'rate_limited' | 'done' | 'failed'
  chunks?: QueueChunk[]
  error?: string
  retry_at?: string | null // ISO time the paused item is scheduled to resume
}

export interface QueueItem extends Omit<QueueEvent, 'chunks'> {
  recentChunks: QueueChunk[]
  seq: number // arrival order, for a stable sort
}

// A stuck job (silent worker kill) is recovered by the worker's beat sweep — it re-enqueues a
// heartbeat-less `processing` job automatically — so the UI no longer guesses at stalls or asks the
// user to re-ingest. QueueItem === Stored now that there's no stall bookkeeping to strip.
type Stored = QueueItem

export function useIngestionQueue(): { items: QueueItem[]; status: string } {
  const [byId, setById] = useState<Record<string, Stored>>({})
  const seqRef = useRef(0)
  const seenRef = useRef<Set<string>>(new Set())
  const queryClient = useQueryClient()

  // Coalesce a burst of settle/snapshot events into one list+stats refetch (see
  // REFRESH_DEBOUNCE_MS). Each qualifying event resets the timer, so the refetch runs once the
  // burst goes quiet rather than once per event.
  const refreshTimer = useRef<ReturnType<typeof setTimeout>>()
  const scheduleJobsRefresh = useCallback(() => {
    clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: qk.jobs })
    }, REFRESH_DEBOUNCE_MS)
  }, [queryClient])
  // Drop a pending refresh on unmount so it can't fire (and refetch) after teardown.
  useEffect(() => () => clearTimeout(refreshTimer.current), [])

  const onMessage = useCallback(
    (msg: QueueEvent) => {
      const isNew = !seenRef.current.has(msg.document_id)
      if (isNew) seenRef.current.add(msg.document_id)
      setById((prev) => {
        const existing = prev[msg.document_id]
        const recentChunks = msg.chunks
          ? [...(existing?.recentChunks ?? []), ...msg.chunks].slice(-RECENT_CHUNKS_CAP)
          : (existing?.recentChunks ?? [])
        const { chunks: _drop, ...rest } = msg
        return {
          ...prev,
          [msg.document_id]: {
            ...rest,
            // A paused event (e.g. rate_limited) may omit progress; keep the last known
            // counts so the card still shows where it stopped (e.g. 128/1436).
            ...carryProgress(msg, existing),
            recentChunks,
            seq: existing?.seq ?? ++seqRef.current,
          },
        }
      })
      // Keep the paginated job history (useJobs) in step with the live socket: refresh when a
      // job first appears (a fresh enqueue) or reaches a terminal state (final row + chunk count).
      // Debounced (scheduleJobsRefresh) so the on-connect snapshot replay and bulk-settle bursts
      // collapse into a single refetch instead of one per event.
      if (isNew || msg.status === 'done' || msg.status === 'failed') {
        scheduleJobsRefresh()
      }
    },
    [scheduleJobsRefresh],
  )

  const { status } = useChannel<QueueEvent>('ingestion-queue', onMessage)

  // Still-in-queue (processing or rate-limited/retrying) first, then most-recent.
  const items: QueueItem[] = Object.values(byId).sort((a, b) => {
    const active = (s: string) => (s === 'processing' || s === 'rate_limited' ? 0 : 1)
    return active(a.status) - active(b.status) || b.seq - a.seq
  })
  return { items, status }
}
