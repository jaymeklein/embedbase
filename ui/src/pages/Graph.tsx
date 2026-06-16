import { useEffect, useState } from 'react'
import { Workflow } from 'lucide-react'
import { useGraph, useWorkspaces } from '../api/hooks'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { Card, EmptyState, QueryError, Select, Skeleton } from '../components/ui'

/** Tag-correlation graph: pick a workspace, see files linked through their tags. */
export default function Graph() {
  const workspaces = useWorkspaces()
  const [wsId, setWsId] = useState('')

  // Default to the first workspace once the list loads.
  useEffect(() => {
    if (!wsId && workspaces.data && workspaces.data.length > 0) {
      setWsId(workspaces.data[0].id)
    }
  }, [wsId, workspaces.data])

  const graph = useGraph(wsId, null)

  return (
    <div className="animate-fade-in space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Graph</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Files linked through the tags they share. Hotter, larger nodes are more-reused tags.
          </p>
        </div>
        <div className="w-56">
          <Select
            value={wsId}
            onChange={(e) => setWsId(e.target.value)}
            disabled={!workspaces.data || workspaces.data.length === 0}
            aria-label="Workspace"
          >
            {workspaces.data?.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name}
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
        isEmpty={!graph.data || graph.data.nodes.length === 0}
        body={graph.data}
      />
    </div>
  )
}

/** Render the canvas across its loading / error / empty / data states. */
function GraphBody({
  isLoading,
  isError,
  message,
  onRetry,
  isEmpty,
  body,
}: {
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  isEmpty: boolean
  body: Parameters<typeof GraphCanvas>[0]['data'] | undefined
}) {
  if (isLoading) return <Skeleton className="h-[70vh] rounded-card" />
  if (isError) return <QueryError title="Could not load the graph" message={message} onRetry={onRetry} />
  if (isEmpty || !body) {
    return (
      <EmptyState
        icon={<Workflow className="h-6 w-6" />}
        title="Nothing to graph yet"
        description="Tag some documents in this workspace and they'll appear here, linked through their shared tags."
      />
    )
  }
  return (
    <Card className="h-[70vh] overflow-hidden p-0">
      <GraphCanvas data={body} />
    </Card>
  )
}
