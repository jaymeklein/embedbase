import { useState } from 'react'
import { Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  useCollections,
  useGrantPermission,
  usePermissions,
  useRevokePermission,
  useWorkspaces,
} from '../../api/hooks'
import type { PermissionLevel, ResourceType, User } from '../../api/types'
import {
  Badge,
  Button,
  CopyButton,
  EmptyState,
  Field,
  Input,
  Modal,
  QueryError,
  Select,
  Skeleton,
  useToast,
} from '../ui'
import { apiErrorMessage } from '../../i18n/apiError'

/** Grantable capabilities — privileges not tied to a resource (backend `_CAPABILITIES`).
 *  Each id must match the backend capability string; its human label + help are resolved
 *  from `users.grants.capabilityLabel.<id>` / `capabilityHelp.<id>` so they follow the language. */
const CAPABILITY_IDS = ['create_workspace', 'manage_tags'] as const
type CapabilityId = (typeof CAPABILITY_IDS)[number]

/**
 * Permission editor for one user. Permissions SCOPE A USER DOWN: with none the user
 * sees and writes everything; each permission narrows what they reach. A workspace
 * permission limits them to that workspace (all its collections); adding a collection
 * (or document) permission narrows further to just that collection (or document).
 * `read` = view-only, `write` = ingest/delete (and read). Existing permissions are
 * listed with an inline revoke.
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
  const { t } = useTranslation()
  const userId = user?.id ?? ''
  const { data, isLoading, isError, error, refetch } = usePermissions(userId, open && Boolean(userId))

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('users.grants.modalTitle', { email: user?.email ?? '' })}
      className="max-w-xl"
    >
      {user && (
        <div className="flex flex-col gap-5">
          <p className="rounded-control border border-border bg-canvas/50 px-3 py-2 text-xs text-ink-muted">
            {t('users.grants.intro.lead')}{' '}
            <span className="font-medium text-ink">{t('users.grants.intro.emphasis')}</span>
            {t('users.grants.intro.rest')}
          </p>
          <GrantForm userId={user.id} />
          {isLoading ? (
            <div className="flex flex-col gap-2">
              {[0, 1].map((i) => (
                <Skeleton key={i} className="h-12 rounded-card" />
              ))}
            </div>
          ) : isError ? (
            <QueryError
              title={t('users.grants.error')}
              message={error ? apiErrorMessage(error, t) : undefined}
              onRetry={() => void refetch()}
            />
          ) : !data || data.length === 0 ? (
            <EmptyState
              icon={<ShieldCheck className="h-7 w-7" />}
              title={t('users.grants.empty.title')}
              description={t('users.grants.empty.description')}
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
                  resourceName={grant.resource_name}
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
  const { t } = useTranslation()
  const [resourceType, setResourceType] = useState<ResourceType>('collection')
  const [wsId, setWsId] = useState('')
  const [colId, setColId] = useState('')
  const [docId, setDocId] = useState('')
  const [capabilityId, setCapabilityId] = useState<CapabilityId>(CAPABILITY_IDS[0])
  const [level, setLevel] = useState<PermissionLevel>('read')
  const toast = useToast()

  const workspaces = useWorkspaces()
  const collections = useCollections(resourceType === 'collection' ? wsId : '')
  const grantMut = useGrantPermission(userId)

  const resourceId =
    resourceType === 'capability'
      ? capabilityId
      : resourceType === 'workspace'
        ? wsId
        : resourceType === 'collection'
          ? colId
          : docId.trim()

  const add = () => {
    if (!resourceId) return
    // A capability grant carries no meaningful level; store write.
    const grantLevel: PermissionLevel = resourceType === 'capability' ? 'write' : level
    grantMut.mutate(
      { resource_type: resourceType, resource_id: resourceId, level: grantLevel },
      {
        onSuccess: () => {
          toast.success(t('users.grants.toast.granted'))
          setDocId('')
        },
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  return (
    <div className="flex flex-col gap-3 rounded-card border border-border p-3.5">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('users.grants.resourceType')}>
          <Select
            value={resourceType}
            onChange={(e) => {
              setResourceType(e.target.value as ResourceType)
              setColId('')
              setDocId('')
            }}
          >
            <option value="workspace">{t('users.grants.type.workspace')}</option>
            <option value="collection">{t('users.grants.type.collection')}</option>
            <option value="document">{t('users.grants.type.document')}</option>
            <option value="capability">{t('users.grants.type.capability')}</option>
          </Select>
        </Field>
        {resourceType !== 'capability' && (
          <Field label={t('users.grants.levelLabel')}>
            <Select value={level} onChange={(e) => setLevel(e.target.value as PermissionLevel)}>
              <option value="read">{t('users.grants.readOption')}</option>
              <option value="write">{t('users.grants.writeOption')}</option>
            </Select>
          </Field>
        )}
      </div>

      {resourceType === 'capability' && (
        <Field label={t('users.grants.type.capability')}>
          <Select
            value={capabilityId}
            onChange={(e) => setCapabilityId(e.target.value as CapabilityId)}
          >
            {CAPABILITY_IDS.map((id) => (
              <option key={id} value={id}>
                {t(`users.grants.capabilityLabel.${id}`)}
              </option>
            ))}
          </Select>
          <p className="mt-1.5 text-xs text-ink-muted">
            {t(`users.grants.capabilityHelp.${capabilityId}`)}
          </p>
        </Field>
      )}

      {resourceType === 'workspace' && (
        <Field label={t('users.grants.type.workspace')}>
          <Select value={wsId} onChange={(e) => setWsId(e.target.value)}>
            <option value="">{t('users.grants.selectWorkspace')}</option>
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
          <Field label={t('users.grants.type.workspace')}>
            <Select
              value={wsId}
              onChange={(e) => {
                setWsId(e.target.value)
                setColId('')
              }}
            >
              <option value="">{t('users.grants.select')}</option>
              {(workspaces.data ?? []).map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('users.grants.type.collection')}>
            <Select value={colId} onChange={(e) => setColId(e.target.value)} disabled={!wsId}>
              <option value="">
                {wsId ? t('users.grants.select') : t('users.grants.pickWorkspaceFirst')}
              </option>
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
        <Field label={t('users.grants.documentId')} hint={t('users.grants.documentIdHint')}>
          <Input value={docId} onChange={(e) => setDocId(e.target.value)} placeholder="doc_…" />
        </Field>
      )}

      <div className="flex justify-end">
        <Button onClick={add} loading={grantMut.isPending} disabled={!resourceId}>
          <Plus className="h-5 w-5" />
          {t('users.grants.addPermission')}
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
  resourceName,
  level,
}: {
  userId: string
  grantId: string
  resourceType: ResourceType
  resourceId: string
  resourceName: string | null
  level: PermissionLevel
}) {
  const { t } = useTranslation()
  const revokeMut = useRevokePermission(userId)
  const [confirming, setConfirming] = useState(false)
  const toast = useToast()
  const revoke = () =>
    revokeMut.mutate(grantId, {
      onSuccess: () => toast.success(t('users.grants.toast.revoked')),
      onError: (e) => toast.error(apiErrorMessage(e, t)),
    })

  // Show the resource's name; the raw id becomes a click-to-copy chip. A deleted
  // resource has no name — say so, but keep the id copyable so the grant is traceable.
  const shortId = resourceId.length > 16 ? `${resourceId.slice(0, 14)}…` : resourceId

  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Badge>{level === 'write' ? t('users.grants.write') : t('users.grants.read')}</Badge>
        <span className="shrink-0 text-xs text-ink-muted">
          {t(`users.grants.type.${resourceType}`)}
        </span>
        <span
          className={
            resourceName
              ? 'min-w-0 truncate text-[13px] text-ink'
              : 'min-w-0 truncate text-[13px] italic text-ink-faint'
          }
          title={resourceName ?? undefined}
        >
          {resourceName ?? t('users.grants.deleted')}
        </span>
        <CopyButton
          text={resourceId}
          label={shortId}
          variant="ghost"
          iconClassName="h-3.5 w-3.5"
          className="shrink-0 font-mono"
          title={t('users.grants.copyIdTitle', { id: resourceId })}
          aria-label={t('users.grants.copyIdAria', { id: resourceId })}
        />
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="danger" size="sm" onClick={revoke} loading={revokeMut.isPending}>
            {t('users.revoke')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={revokeMut.isPending}>
            {t('common.cancel')}
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('users.grants.revokeAria')}
          onClick={() => setConfirming(true)}
          className="h-10 w-10 shrink-0 px-0 hover:text-err"
        >
          <Trash2 className="h-7 w-7" />
        </Button>
      )}
    </div>
  )
}
