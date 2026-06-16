import { useEffect, useRef } from 'react'
import { Graph } from '@antv/g6'
import type { GraphResponse } from '../../api/types'
import { toG6 } from '../../lib/toG6'

/**
 * Mounts an AntV G6 v5 canvas for the tag-correlation graph. A fresh instance is
 * built per `data` (workspace/scope switch) and disposed on cleanup. Destroy is
 * deferred until the in-flight async `render()` settles, so React 18 StrictMode's
 * double-mount doesn't operate on an already-destroyed graph. Pan/zoom/drag/select
 * behaviors are built in; the cold→hot tag styling comes from {@link toG6}.
 */
export function GraphCanvas({ data }: { data: GraphResponse }) {
  const containerRef = useRef<HTMLDivElement>(null)

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
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
    })
    const rendered = graph.render()
    return () => {
      void rendered.finally(() => graph.destroy())
    }
  }, [data])

  return <div ref={containerRef} className="h-full w-full" />
}
