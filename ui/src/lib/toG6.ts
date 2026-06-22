import type { GraphData, NodeData } from '@antv/g6'
import type { GraphResponse } from '../api/types'

/** Document (file) nodes — neutral slate, fixed; the larger of the two kinds. */
const FILE_FILL = '#64748b'
const FILE_SIZE = 26
/** Tag hubs — smaller than documents; size grows with heat, fill ramps cold→hot.
 * The hottest tag (TAG_MIN_SIZE + TAG_SIZE_RANGE = 22) still reads smaller than a file. */
const TAG_MIN_SIZE = 10
const TAG_SIZE_RANGE = 12
const COLD: [number, number, number] = [37, 99, 235] // #2563eb
const HOT: [number, number, number] = [220, 38, 38] // #dc2626

/** Linear-interpolate the cold→hot ramp at `pct` (clamped to [0,1]). */
function heatColor(pct: number): string {
  const t = Math.max(0, Math.min(1, pct))
  const [r, g, b] = COLD.map((c, i) => Math.round(c + (HOT[i] - c) * t))
  return `rgb(${r}, ${g}, ${b})`
}

/**
 * Map a {@link GraphResponse} to G6 v5 graph data. Tag hubs are sized and
 * coloured by `heat_pct` so the most-reused tags read as the largest, hottest
 * nodes; files stay small and neutral. Pure — no G6 import, unit-testable.
 */
export function toG6(graph: GraphResponse): GraphData {
  const nodes: NodeData[] = graph.nodes.map((n) => {
    const isTag = n.kind === 'tag'
    return {
      id: n.id,
      data: { label: n.label, kind: n.kind },
      style: {
        size: isTag ? TAG_MIN_SIZE + n.heat_pct * TAG_SIZE_RANGE : FILE_SIZE,
        fill: isTag ? heatColor(n.heat_pct) : FILE_FILL,
        labelText: n.label,
      },
    }
  })
  const edges = graph.edges.map((e) => ({ source: e.source, target: e.target }))
  return { nodes, edges }
}
