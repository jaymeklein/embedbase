import { useEffect, useRef } from 'react'
import { Graph } from '@antv/g6'
import type { GraphResponse } from '../../api/types'
import { toG6 } from '../../lib/toG6'

/** Read a node id off a G6 element event without depending on its internal types. */
function eventNodeId(e: unknown): string | undefined {
  return (e as { target?: { id?: string } }).target?.id
}

/**
 * Mounts an AntV G6 v5 canvas for the tag-correlation graph. A fresh instance is
 * built per `data` (workspace/scope switch) and disposed on cleanup. Destroy is
 * deferred until the in-flight async `render()` settles, so React 18 StrictMode's
 * double-mount doesn't operate on an already-destroyed graph.
 *
 * Hovering a node highlights its 2-hop neighbourhood (file↔tag↔files); clicking a
 * node reports its id via `onSelect` (and the empty canvas clears it). Bumping
 * `fitNonce` re-fits the view. Cold→hot tag styling comes from {@link toG6}.
 */
export function GraphCanvas({
  data,
  onSelect,
  fitNonce = 0,
}: {
  data: GraphResponse
  onSelect?: (nodeId: string | null) => void
  fitNonce?: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  // Keep the latest onSelect without forcing a graph rebuild on identity change.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  useEffect(() => {
    if (!containerRef.current) return
    const graph = new Graph({
      container: containerRef.current,
      autoFit: 'view',
      data: toG6(data),
      layout: { type: 'd3-force', collide: { radius: 30 } },
      node: {
        // labelText is set per-node in toG6; these are the shared label defaults.
        style: { labelPlacement: 'bottom', labelFill: '#475569', labelFontSize: 10 },
      },
      edge: { style: { stroke: '#cbd5e1', lineWidth: 1 } },
      behaviors: [
        'drag-canvas',
        'zoom-canvas',
        'drag-element',
        'click-select',
        { type: 'hover-activate', degree: 2 },
        'auto-adapt-label',
      ],
    })
    graphRef.current = graph
    graph.on('node:click', (e: unknown) => {
      const id = eventNodeId(e)
      if (id) onSelectRef.current?.(id)
    })
    graph.on('canvas:click', () => onSelectRef.current?.(null))
    const rendered = graph.render()
    return () => {
      graphRef.current = null
      void rendered.finally(() => graph.destroy())
    }
  }, [data])

  // Re-fit on demand (Reset view button bumps the nonce).
  useEffect(() => {
    if (fitNonce > 0) void graphRef.current?.fitView()
  }, [fitNonce])

  return <div ref={containerRef} className="h-full w-full" />
}
