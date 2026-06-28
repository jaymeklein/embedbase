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
import { useChannel } from './useChannel'

export type IngestPhase = 'parsing' | 'embedding' | 'storing' | 'done' | 'failed'

export interface IngestionProgress {
  document_id: string
  collection_id: string
  phase: IngestPhase
  current: number | null
  total: number | null
  pct: number | null
  status: 'processing' | 'done' | 'failed'
}

export function useIngestionProgress(
  wsId: string,
  colId: string,
): Record<string, IngestionProgress> {
  const queryClient = useQueryClient()
  const [progress, setProgress] = useState<Record<string, IngestionProgress>>({})

  const onMessage = useCallback(
    (msg: IngestionProgress) => {
      setProgress((prev) => ({ ...prev, [msg.document_id]: msg }))
      if (msg.status === 'done' || msg.status === 'failed') {
        void queryClient.invalidateQueries({ queryKey: qk.documents(wsId, colId) })
      }
    },
    [queryClient, wsId, colId],
  )

  useChannel<IngestionProgress>(colId ? `ingestion:${colId}` : null, onMessage)
  return progress
}
