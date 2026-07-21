import { useState } from 'react'
import { Plus, ShieldCheck, Trash2 } from 'lucide-react'
import {
  useCollections,
  useGrantPermission,
  usePermissions,
  useRevokePermission,
  useWorkspaces,
} from '../../api/hooks'
import type { PermissionLevel, ResourceType, User } from '../../api/types'
import { Badge, Button, EmptyState, Field, Input, Modal, QueryError, Select, Skeleton, useToast } from '../ui'

/**
 * Grant editor for one user: pick a resource (a whole workspace, one collection,
 * or a single document id) and a level (read/write), then add it. A workspace
 * grant cascades to its collections and documents; write implies read. Existing
 * grants are listed with an inline revoke.
 */
export function PermissionsModal({
  open,
  user,
  onClose,
}: {
  open: boolean
  user: User | null
  onClose: () => void
}) {
  const userId = user?.id ?? ''
  const { data, isLoading, isError, error, refetch } = usePermissions(userId, open && Boolean(userId))

  return (
    <Modal open={open} onClose={onClose} title={`Permissions — ${user?.email ?? ''}`} className="max-w-xl">
      {user && (
        <div className="flex flex-col gap-5">
          <GrantForm userId={user.id} />
          {isLoading ? (
            <div className="flex flex-col gap-2">
              {[0, 1].map((i) => (
                <Skeleton key={i} className="h-12 rounded-card" />
              ))}
            </div>
          ) : isError ? (
            <QueryError title="Could not load grants" message={error?.message} onRetry={() => void refetch()} />
          ) : !data || data.length === 0 ? (
            <EmptyState
              icon={<ShieldCheck className="h-7 w-7" />}
              title="No permissions yet"
              description="Grant this user read or write on a workspace, collection, or document above."
            />
          ) : (
            <div className="divide-y divide-border rounded-card border border-border">
              {data.map((grant) => (
                <GrantRow
                  key={grant.id}
                  userId={user.id}
                  grantId={grant.id}
                  resourceType={grant.resource_type}
                  resourceId={grant.resource_id}
                  level={grant.level}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

/** The add-a-grant form: resource type → resource picker → level → add. */
function GrantForm({ userId }: { userId: string }) {
  const [resourceType, setResourceType] = useState<ResourceType>('collection')
  const [wsId, setWsId] = useState('')
  const [colId, setColId] = useState('')
  const [docId, setDocId] = useState('')
  const [level, setLevel] = useState<PermissionLevel>('read')
  const toast = useToast()

  const workspaces = useWorkspaces()
  const collections = useCollections(resourceType === 'collection' ? wsId : '')
  const grantMut = useGrantPermission(userId)

  const resourceId =
    resourceType === 'workspace' ? wsId : resourceType === 'collection' ? colId : docId.trim()

  const add = () => {
    if (!resourceId) return
    grantMut.mutate(
      { resource_type: resourceType, resource_id: resourceId, level },
      {
        onSuccess: () => {
          toast.success('Permission granted.')
          setDocId('')
        },
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <div className="flex flex-col gap-3 rounded-card border border-border p-3.5">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Resource type">
          <Select
            value={resourceType}
            onChange={(e) => {
              setResourceType(e.target.value as ResourceType)
              setColId('')
              setDocId('')
            }}
          >
            <option value="workspace">Workspace</option>
            <option value="collection">Collection</option>
            <option value="document">Document</option>
          </Select>
        </Field>
        <Field label="Level">
          <Select value={level} onChange={(e) => setLevel(e.target.value as PermissionLevel)}>
            <option value="read">Read — search, list, download</option>
            <option value="write">Write — ingest, delete (and read)</option>
          </Select>
        </Field>
      </div>

      {resourceType === 'workspace' && (
        <Field label="Workspace">
          <Select value={wsId} onChange={(e) => setWsId(e.target.value)}>
            <option value="">Select a workspace…</option>
            {(workspaces.data ?? []).map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name}
              </option>
            ))}
          </Select>
        </Field>
      )}

      {resourceType === 'collection' && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Workspace">
            <Select
              value={wsId}
              onChange={(e) => {
                setWsId(e.target.value)
                setColId('')
              }}
            >
              <option value="">Select…</option>
              {(workspaces.data ?? []).map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Collection">
            <Select value={colId} onChange={(e) => setColId(e.target.value)} disabled={!wsId}>
              <option value="">{wsId ? 'Select…' : 'Pick a workspace first'}</option>
              {(collections.data ?? []).map((col) => (
                <option key={col.id} value={col.id}>
                  {col.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}

      {resourceType === 'document' && (
        <Field label="Document ID" hint="The document's id (e.g. doc_…), from its collection.">
          <Input value={docId} onChange={(e) => setDocId(e.target.value)} placeholder="doc_…" />
        </Field>
      )}

      <div className="flex justify-end">
        <Button onClick={add} loading={grantMut.isPending} disabled={!resourceId}>
          <Plus className="h-5 w-5" />
          Add permission
        </Button>
      </div>
    </div>
  )
}

/** A single grant row with an inline-confirmed revoke. */
function GrantRow({
  userId,
  grantId,
  resourceType,
  resourceId,
  level,
}: {
  userId: string
  grantId: string
  resourceType: ResourceType
  resourceId: string
  level: PermissionLevel
}) {
  const revokeMut = useRevokePermission(userId)
  const [confirming, setConfirming] = useState(false)
  const toast = useToast()
  const revoke = () =>
    revokeMut.mutate(grantId, {
      onSuccess: () => toast.success('Permission revoked.'),
      onError: (e) => toast.error(e.message),
    })

  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Badge>{level === 'write' ? 'Write' : 'Read'}</Badge>
        <span className="text-xs capitalize text-ink-muted">{resourceType}</span>
        <code className="truncate font-mono text-[13px] text-ink">{resourceId}</code>
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="danger" size="sm" onClick={revoke} loading={revokeMut.isPending}>
            Revoke
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={revokeMut.isPending}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          aria-label="Revoke permission"
          onClick={() => setConfirming(true)}
          className="h-7 w-7 shrink-0 px-0 hover:text-err"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}
