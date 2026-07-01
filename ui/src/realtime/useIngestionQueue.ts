/**
 * Live, global ingestion queue over ONE WebSocket (topic `ingestion-queue`, fed by
 * the worker alongside the per-collection `ingestion:{id}` topic). Built on the
 * generic {@link useChannel}.
 *
 * Tracks every in-flight document plus those that finished/failed recently (the
 * backend snapshot replays the last hour on connect). Per file it accumulates the
 * most recent chunk labels — capped, so a 1400-chunk PDF can't grow unbounded.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useChannel } from './useChannel'
import type { IngestPhase } from './useIngestionProgress'

/** Cap on the live per-file chunk list — newest kept, older dropped. */
const RECENT_CHUNKS_CAP = 50

/** A processing item that emits no progress for this long is treated as stalled. A
 *  silent worker kill/crash can't emit a `failed` event, so the queue would otherwise
 *  show it frozen mid-chunk forever. Only applied to the frequently-emitting phases
 *  (embedding/storing) so an opaque docling parse isn't flagged. Generous: a single
 *  CPU batch is bounded well under this by the embed adapter's per-request timeout. */
const STALL_MS = 180_000

export interface QueueChunk {
  index: number
  label: string
}

interface QueueEvent {
  document_id: string
  collection_id: string
  collection_name: string
  filename: string
  phase: IngestPhase
  current: number | null
  total: number | null
  pct: number | null
  status: 'processing' | 'done' | 'failed'
  chunks?: QueueChunk[]
  error?: string
}

export interface QueueItem extends Omit<QueueEvent, 'chunks'> {
  recentChunks: QueueChunk[]
  seq: number // arrival order, for a stable sort
  stalled: boolean // processing but no progress for STALL_MS (likely a silent kill)
}

interface Stored extends Omit<QueueEvent, 'chunks'> {
  recentChunks: QueueChunk[]
  seq: number
  lastAt: number // ms of the last event for this doc — drives stall detection
}

export function useIngestionQueue(): { items: QueueItem[]; status: string } {
  const [byId, setById] = useState<Record<string, Stored>>({})
  const seqRef = useRef(0)
  const [, setTick] = useState(0)

  const onMessage = useCallback((msg: QueueEvent) => {
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
          recentChunks,
          seq: existing?.seq ?? ++seqRef.current,
          lastAt: Date.now(),
        },
      }
    })
  }, [])

  const { status } = useChannel<QueueEvent>('ingestion-queue', onMessage)

  // Re-evaluate stall as wall-clock advances, even when no events arrive.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 5000)
    return () => clearInterval(id)
  }, [])

  const now = Date.now()
  const items: QueueItem[] = Object.values(byId)
    .map(({ lastAt, ...it }) => ({
      ...it,
      stalled:
        it.status === 'processing' &&
        (it.phase === 'embedding' || it.phase === 'storing') &&
        now - lastAt > STALL_MS,
    }))
    // Processing first, then most-recently-arrived.
    .sort((a, b) => {
      const ap = a.status === 'processing' ? 0 : 1
      const bp = b.status === 'processing' ? 0 : 1
      return ap - bp || b.seq - a.seq
    })
  return { items, status }
}
