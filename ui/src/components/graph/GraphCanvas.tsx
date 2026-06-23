import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Graph } from '@antv/g6'
import { Magnet, Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import type { GraphResponse } from '../../api/types'
import { toG6 } from '../../lib/toG6'
import { Button } from '../ui'

/** Repel force (node-to-node repulsion) bounds. The UI shows a positive magnitude;
 *  the d3-force `manyBody.strength` is its negation. This is the tunable graph config. */
const DEFAULT_REPEL = 60
const MIN_REPEL = 10
const MAX_REPEL = 240

/** Read a node id off a G6 element event without depending on its internal types. */
function eventNodeId(e: unknown): string | undefined {
  return (e as { target?: { id?: string } }).target?.id
}

/**
 * d3-force layout config for a given repel magnitude. `manyBody` repels every node
 * (locked/dragged nodes keep exerting charge too); `collide` stops overlap; links
 * pull connected nodes together. The `x`/`y` positional forces are essential: a
 * graph with disconnected tag-clusters has nothing else holding it together, so
 * without them repulsion drifts the pieces apart forever and every reset/reheat
 * flings them further out — they pull every node toward the centre, bounding the
 * layout so re-runs re-converge. verified against @antv/g6@5.1.1 (option schema:
 * node_modules/@antv/layout d3-force) + runtime.
 */
function forceLayout(repel: number) {
  return {
    type: 'd3-force' as const,
    link: { distance: 70, strength: 0.4 },
    // `repel` is the raw slider value; reverse it into the charge magnitude so the
    // control reads the right way round (it felt inverted before). distanceMax caps
    // repulsion range so distant clusters don't shove each other across the canvas.
    manyBody: { strength: -(MIN_REPEL + MAX_REPEL - repel), distanceMax: 600 },
    collide: { radius: 30, strength: 1 },
    // x/y positional forces pull every node toward the centre — without them a graph
    // with disconnected tag-clusters drifts apart forever and reset/reheat flings
    // nodes outward. Kept gentle so repulsion + collide can still spread the cluster
    // out (too strong here crushes everything into an overlapping blob at the centre).
    x: { strength: 0.045 },
    y: { strength: 0.045 },
  }
}

/**
 * Reach the live d3-force *simulation* — the same path G6's DragElementForce uses
 * to pin nodes. `getLayoutInstance()` returns layout adapters keyed by `id`; the
 * simulation with `setFixedPosition`/`reheat` lives on the adapter's `.instance`.
 * Internal API, so every hop is optional-chained: a future version that moves it
 * degrades click-to-unlock to a no-op rather than crashing. verified against
 * @antv/g6@5.1.1 source + runtime.
 */
type ForceSim = {
  setFixedPosition?: (id: string, pos: [number | null, number | null, number | null]) => void
  reheat?: (alpha?: number) => void
  restart?: () => void
}
function forceSim(graph: Graph): ForceSim | undefined {
  const ctx = (
    graph as unknown as {
      context?: {
        layout?: { getLayoutInstance?: () => Array<{ id?: string; instance?: ForceSim }> }
      }
    }
  ).context
  const adapter = (ctx?.layout?.getLayoutInstance?.() ?? []).find((l) => l?.id === 'd3-force')
  return adapter?.instance
}

/** Wake the force simulation so released nodes drift back into place. */
function reheat(graph: Graph): void {
  const sim = forceSim(graph)
  if (sim?.reheat) sim.reheat(0.5)
  else sim?.restart?.()
}

/**
 * Mounts an AntV G6 v5 canvas for the tag-correlation graph. A fresh instance is
 * built per `data` (workspace/scope switch) and disposed on cleanup. Destroy is
 * deferred until the in-flight async `render()` settles, so React 18 StrictMode's
 * double-mount doesn't operate on an already-destroyed graph.
 *
 * Nodes repel each other (tunable repel-force control); dragging a node pins it in
 * place, clicking it releases it back into the simulation. Both dropping a drag and
 * clicking select the node — `selected` (the controlled id) drives the highlight, so
 * the indicator shows in both cases. Hovering highlights the 2-hop neighbourhood.
 * Bumping `fitNonce` resets the layout — every node returns to a fresh centered,
 * non-overlapping cluster. Cold→hot tag styling and sizing come from {@link toG6}.
 */
export function GraphCanvas({
  data,
  onSelect,
  selected = null,
  fitNonce = 0,
}: {
  data: GraphResponse
  onSelect?: (nodeId: string | null) => void
  selected?: string | null
  fitNonce?: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  // Keep the latest onSelect without forcing a graph rebuild on identity change.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  // Repel magnitude is read by reference at graph-build time so changing it doesn't
  // rebuild the graph (it's applied live via setOptions instead).
  const [repel, setRepel] = useState(DEFAULT_REPEL)
  const repelRef = useRef(repel)
  repelRef.current = repel

  useEffect(() => {
    if (!containerRef.current) return
    const graph = new Graph({
      container: containerRef.current,
      autoFit: 'view',
      data: toG6(data),
      layout: forceLayout(repelRef.current),
      node: {
        // labelText is set per-node in toG6; these are the shared label defaults.
        style: { labelPlacement: 'bottom', labelFill: '#475569', labelFontSize: 10 },
      },
      edge: { style: { stroke: '#cbd5e1', lineWidth: 1 } },
      behaviors: [
        'drag-canvas',
        'zoom-canvas',
        // drag-element-force (not plain drag-element) keeps the dragged node in the
        // force simulation, so its edges and connected tags follow it; fixed pins it
        // where dropped. verified against @antv/g6@5.1.1 via Context7.
        { type: 'drag-element-force', fixed: true },
        // No 'click-select': selection (the highlight) is driven by the `selected`
        // prop instead, so dropping a drag and clicking both show the indicator.
        { type: 'hover-activate', degree: 2 },
        'auto-adapt-label',
      ],
    })
    graphRef.current = graph
    graph.on('node:click', (e: unknown) => {
      const id = eventNodeId(e)
      if (!id) return
      // Clicking releases a drag-locked node so the force layout reclaims it.
      forceSim(graph)?.setFixedPosition?.(id, [null, null, null])
      reheat(graph)
      onSelectRef.current?.(id)
    })
    // Dropping a drag pins the node (drag-element-force fixed) and selects it too,
    // so the highlight appears without a follow-up click.
    graph.on('node:dragend', (e: unknown) => {
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

  // Drive the node highlight off the controlled `selected` id: clear the previous
  // node's state, mark the current one. Both click and drag-drop call onSelect, so
  // the indicator appears in both cases from this one place.
  const highlightedRef = useRef<string | null>(null)
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    const prev = highlightedRef.current
    if (prev && prev !== selected) graph.setElementState(prev, [])
    if (selected) graph.setElementState(selected, ['selected'])
    highlightedRef.current = selected
    // ponytail: keyed on `selected` only; a `data` rebuild drops the ring, but
    // selection resets on scope change so it's not worth the render-timing dance.
  }, [selected])

  // Reset (Reset-view button bumps the nonce): a full re-layout rebuilds the
  // simulation from node data, which drops every drag-lock (those live only in the
  // sim) and re-converges all nodes to a fresh centered, repel-spaced cluster.
  useEffect(() => {
    if (fitNonce <= 0) return
    const graph = graphRef.current
    if (!graph) return
    void graph.layout().then(() => graph.fitView())
  }, [fitNonce])

  // Apply a changed repel force to the live layout (the tunable graph config).
  const applyRepel = (value: number) => {
    const graph = graphRef.current
    if (!graph) return
    graph.setOptions({ layout: forceLayout(value) })
    void graph.layout().then(() => graph.fitView())
  }

  // verified against @antv/g6@5.1.1 via Context7: zoomBy(ratio, animation), fitView().
  const anim = { duration: 150 }
  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      <div className="absolute bottom-3 right-3 flex flex-col items-end gap-2">
        <RepelControl value={repel} onChange={setRepel} onCommit={applyRepel} />
        <div className="flex flex-col gap-1.5">
          <ControlButton label="Zoom in" onClick={() => void graphRef.current?.zoomBy(1.2, anim)}>
            <ZoomIn className="h-6 w-6" />
          </ControlButton>
          <ControlButton label="Zoom out" onClick={() => void graphRef.current?.zoomBy(0.8, anim)}>
            <ZoomOut className="h-6 w-6" />
          </ControlButton>
          <ControlButton label="Fit to view" onClick={() => void graphRef.current?.fitView()}>
            <Maximize2 className="h-6 w-6" />
          </ControlButton>
        </div>
      </div>
    </div>
  )
}

/** Slider that tunes the node repel force; applies on release so the layout isn't
 *  re-run on every intermediate value. */
function RepelControl({
  value,
  onChange,
  onCommit,
}: {
  value: number
  onChange: (v: number) => void
  onCommit: (v: number) => void
}) {
  return (
    <div
      className="flex items-center gap-2 rounded-control border border-border bg-surface/90 px-2.5 py-1.5 shadow-overlay backdrop-blur"
      title="Repel force — how strongly nodes push each other apart"
    >
      <Magnet className="h-5 w-5 shrink-0 text-ink-muted" />
      <input
        type="range"
        min={MIN_REPEL}
        max={MAX_REPEL}
        value={value}
        aria-label="Repel force"
        onChange={(e) => onChange(Number(e.target.value))}
        onPointerUp={() => onCommit(value)}
        onKeyUp={() => onCommit(value)}
        className="h-1.5 w-28 cursor-pointer accent-accent"
      />
    </div>
  )
}

/** A square icon button for the canvas viewport toolbar. */
function ControlButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="h-9 w-9 px-0"
    >
      {children}
    </Button>
  )
}
