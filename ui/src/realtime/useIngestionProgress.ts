/**
 * Live ingestion progress for every document in a collection, over ONE WebSocket
 * (topic `ingestion:{collection_id}`). Built on the generic {@link useChannel}.
 *
 * Returns a map keyed by `document_id`. On a terminal event (`done`/`failed`) it
 * invalidates the documents query once so the row picks up its final persisted
 * state (chunk count, index flag, failure reason) — replacing the old 2s poll.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { qk } from '../api/hooks'
import { carryProgress } from './carryProgress'
import { useChannel } from './useChannel'

export type IngestPhase =
  | 'parsing'
  | 'embedding'
  | 'storing'
  | 'rate_limited'
  | 'done'
  | 'failed'

export interface IngestionProgress {
  document_id: string
  collection_id: string
  phase: IngestPhase
  current: number | null
  total: number | null
  pct: number | null
  status: 'processing' | 'rate_limited' | 'done' | 'failed'
}

export function useIngestionProgress(
  wsId: string,
  colId: string,
): Record<string, IngestionProgress> {
  const queryClient = useQueryClient()
  const [progress, setProgress] = useState<Record<string, IngestionProgress>>({})

  const onMessage = useCallback(
    (msg: IngestionProgress) => {
      // A paused event (e.g. rate_limited) may omit progress counts; keep the last
      // known ones so the row still shows where it stopped.
      setProgress((prev) => {
        const existing = prev[msg.document_id]
        return {
          ...prev,
          [msg.document_id]: {
            ...msg,
            ...carryProgress(msg, existing),
          },
        }
      })
      if (msg.status === 'done' || msg.status === 'failed') {
        void queryClient.invalidateQueries({ queryKey: qk.documents(wsId, colId) })
      }
    },
    [queryClient, wsId, colId],
  )

  useChannel<IngestionProgress>(colId ? `ingestion:${colId}` : null, onMessage)
  return progress
}
