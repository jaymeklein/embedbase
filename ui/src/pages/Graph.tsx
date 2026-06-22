import { useEffect, useMemo, useState } from 'react'
import { RotateCcw, Workflow, X } from 'lucide-react'
import { useCollections, useGraph, useWorkspaces } from '../api/hooks'
import type { GraphNode, GraphResponse } from '../api/types'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { Badge, Button, Card, EmptyState, QueryError, Select, Skeleton } from '../components/ui'

/** Tag-correlation graph: pick a scope, see files linked through their tags. */
export default function Graph() {
  const workspaces = useWorkspaces()
  const [wsId, setWsId] = useState('')
  const [colId, setColId] = useState('') // '' = whole workspace
  const [selected, setSelected] = useState<string | null>(null)
  const [fitNonce, setFitNonce] = useState(0)

  // Default to the first workspace once the list loads.
  useEffect(() => {
    if (!wsId && workspaces.data && workspaces.data.length > 0) {
      setWsId(workspaces.data[0].id)
    }
  }, [wsId, workspaces.data])

  // Reset the collection filter + selection whenever the workspace changes.
  useEffect(() => {
    setColId('')
    setSelected(null)
  }, [wsId])

  const collections = useCollections(wsId)
  const graph = useGraph(wsId, colId || null)

  return (
    <div className="animate-fade-in space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Graph</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Files (the larger nodes) linked through the tags they share. Hotter tag nodes are
            more-reused. Drag a node to pin and highlight it, click it to release; tune the repel
            force or reset to re-center.
          </p>
        </div>
        <div className="flex gap-2">
          <Select
            value={wsId}
            onChange={(e) => setWsId(e.target.value)}
            disabled={!workspaces.data || workspaces.data.length === 0}
            aria-label="Workspace"
            className="w-44"
          >
            {workspaces.data?.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name}
              </option>
            ))}
          </Select>
          <Select
            value={colId}
            onChange={(e) => setColId(e.target.value)}
            aria-label="Collection"
            className="w-44"
          >
            <option value="">All collections</option>
            {collections.data?.map((col) => (
              <option key={col.id} value={col.id}>
                {col.name}
              </option>
            ))}
          </Select>
        </div>
      </header>

      <GraphBody
        isLoading={graph.isLoading || workspaces.isLoading}
        isError={graph.isError}
        message={graph.error?.message}
        onRetry={() => void graph.refetch()}
        data={graph.data}
        selected={selected}
        onSelect={setSelected}
        fitNonce={fitNonce}
        onReset={() => {
          setSelected(null)
          setFitNonce((n) => n + 1)
        }}
      />
    </div>
  )
}

/** Render the canvas (with legend, reset, detail panel) across its states. */
function GraphBody({
  isLoading,
  isError,
  message,
  onRetry,
  data,
  selected,
  onSelect,
  fitNonce,
  onReset,
}: {
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  data: GraphResponse | undefined
  selected: string | null
  onSelect: (id: string | null) => void
  fitNonce: number
  onReset: () => void
}) {
  if (isLoading) return <Skeleton className="h-[70vh] rounded-card" />
  if (isError) return <QueryError title="Could not load the graph" message={message} onRetry={onRetry} />
  if (!data || data.nodes.length === 0) {
    return (
      <EmptyState
        icon={<Workflow className="h-6 w-6" />}
        title="Nothing to graph yet"
        description="Tag some documents in this scope and they'll appear here, linked through their shared tags."
      />
    )
  }
  return (
    <Card className="relative h-[70vh] overflow-hidden p-0">
      <GraphCanvas data={data} onSelect={onSelect} selected={selected} fitNonce={fitNonce} />
      <HeatLegend maxHeat={data.max_heat} />
      <Button
        variant="secondary"
        size="sm"
        onClick={onReset}
        className="absolute right-3 top-3"
        aria-label="Reset view"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        Reset
      </Button>
      {selected && <DetailPanel graph={data} nodeId={selected} onClose={() => onSelect(null)} />}
    </Card>
  )
}

/** Cold→hot gradient key for tag heat, anchored to the busiest tag. */
function HeatLegend({ maxHeat }: { maxHeat: number }) {
  if (maxHeat <= 0) return null
  return (
    <div className="absolute bottom-3 left-3 rounded-control border border-border bg-surface/90 px-3 py-2 text-xs text-ink-muted backdrop-blur">
      <div className="mb-1 font-medium text-ink">Tag heat</div>
      <div className="flex items-center gap-2">
        <span>1</span>
        <span
          className="h-2 w-24 rounded-full"
          style={{ background: 'linear-gradient(to right, rgb(37,99,235), rgb(220,38,38))' }}
        />
        <span>{maxHeat} files</span>
      </div>
    </div>
  )
}

/** Side panel describing the clicked node and its immediate links. */
function DetailPanel({
  graph,
  nodeId,
  onClose,
}: {
  graph: GraphResponse
  nodeId: string
  onClose: () => void
}) {
  const node = useMemo(() => graph.nodes.find((n) => n.id === nodeId), [graph, nodeId])
  const neighbors = useMemo(() => neighborLabels(graph, nodeId), [graph, nodeId])
  if (!node) return null
  return (
    <div className="absolute right-3 top-14 w-64 rounded-card border border-border bg-surface/95 p-4 shadow-overlay backdrop-blur">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="text-[11px] uppercase tracking-wide text-ink-faint">{node.kind}</span>
          <h3 className="truncate text-sm font-medium text-ink">{node.label}</h3>
        </div>
        <button onClick={onClose} className="text-ink-faint hover:text-ink" aria-label="Close">
          <X className="h-4 w-4" />
        </button>
      </div>
      <NodeFacts node={node} />
      {neighbors.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wide text-ink-faint">
            {node.kind === 'tag' ? 'Files' : 'Tags'}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {neighbors.map((label) => (
              <Badge key={label}>{label}</Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** The kind-specific stat line for a node. */
function NodeFacts({ node }: { node: GraphNode }) {
  if (node.kind === 'tag') {
    return (
      <p className="mt-2 text-[13px] text-ink-muted">
        Used by <span className="font-medium text-ink">{node.heat}</span> file
        {node.heat === 1 ? '' : 's'}.
      </p>
    )
  }
  const type = typeof node.meta.file_type === 'string' ? node.meta.file_type : null
  return (
    <p className="mt-2 text-[13px] text-ink-muted">
      {type ? `${type.toUpperCase()} · ` : ''}
      {node.degree} tag{node.degree === 1 ? '' : 's'}.
    </p>
  )
}

/** Labels of the nodes one edge away from `nodeId`, resolved through the node map. */
function neighborLabels(graph: GraphResponse, nodeId: string): string[] {
  const byId = new Map(graph.nodes.map((n) => [n.id, n.label]))
  const labels: string[] = []
  for (const e of graph.edges) {
    if (e.source === nodeId) labels.push(byId.get(e.target) ?? e.target)
    else if (e.target === nodeId) labels.push(byId.get(e.source) ?? e.source)
  }
  return labels
}
