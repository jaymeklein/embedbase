import { useState } from 'react'
import { KeyRound, Pencil, Plus, RotateCcw, ShieldCheck, Trash2, Users as UsersIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  useCreateUser,
  useDeleteUser,
  useResetUserPassword,
  useUpdateUser,
  useUsers,
} from '../api/hooks'
import type { User, UserUpdate } from '../api/types'
import {
  Badge,
  Button,
  ConfirmDialog,
  DropdownItem,
  DropdownMenu,
  EmptyState,
  Modal,
  QueryError,
  Skeleton,
  useToast,
} from '../components/ui'
import { UserFormModal, type UserFormValues } from '../components/users/UserFormModal'
import { UserKeyModal } from '../components/users/UserKeyModal'
import { PermissionsModal } from '../components/users/PermissionsModal'
import { RevealOncePanel } from '../components/users/RevealOncePanel'
import { apiErrorMessage } from '../i18n/apiError'
import { cn } from '../lib/cn'
import { formatDate } from '../lib/format'

/** Which dialog (if any) is currently open, plus the row it acts on. */
type Dialog =
  | { kind: 'none' }
  | { kind: 'create' }
  | { kind: 'edit'; user: User }
  | { kind: 'delete'; user: User }
  | { kind: 'key'; user: User }
  | { kind: 'permissions'; user: User }
  | { kind: 'reset'; user: User }
  | { kind: 'reveal'; title: string; message: string; secret: string }

/** Reduce a full form submission to only the fields that actually changed. */
function changedFields(user: User, values: UserFormValues): UserUpdate {
  const body: UserUpdate = {}
  if (values.username !== user.username) body.username = values.username
  if (values.email !== (user.email ?? '')) body.email = values.email
  if (values.name !== (user.name ?? '')) body.name = values.name
  if (values.is_active !== user.is_active) body.is_active = values.is_active
  if (values.is_admin !== user.is_admin) body.is_admin = values.is_admin
  if (values.rate_limit_rpm !== user.rate_limit_rpm) body.rate_limit_rpm = values.rate_limit_rpm
  return body
}

/** Users admin: sign-in accounts (username/password + role), API keys, and grants. */
export default function Users() {
  const { t } = useTranslation()
  const { data, isLoading, isError, error, refetch } = useUsers()
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' })
  const close = () => setDialog({ kind: 'none' })
  const toast = useToast()
  const createMut = useCreateUser()
  const updateMut = useUpdateUser()
  const deleteMut = useDeleteUser()
  const resetMut = useResetUserPassword()

  const handleSubmit = (values: UserFormValues) => {
    if (dialog.kind === 'create') {
      createMut.mutate(values, {
        onSuccess: (user) =>
          setDialog({
            kind: 'reveal',
            title: t('users.reveal.created.title', { username: user.username }),
            message: t('users.reveal.created.message'),
            secret: user.temp_password,
          }),
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      })
    } else if (dialog.kind === 'edit') {
      const body = changedFields(dialog.user, values)
      if (Object.keys(body).length === 0) {
        close()
        return
      }
      updateMut.mutate(
        { id: dialog.user.id, body },
        {
          onSuccess: () => {
            toast.success(t('users.toast.updated'))
            close()
          },
          onError: (e) => toast.error(apiErrorMessage(e, t)),
        },
      )
    }
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { user } = dialog
    deleteMut.mutate(user.id, {
      onSuccess: () => {
        toast.success(t('users.toast.deleted', { username: user.username }))
        close()
      },
      onError: (e) => toast.error(apiErrorMessage(e, t)),
    })
  }

  const handleReset = () => {
    if (dialog.kind !== 'reset') return
    const { user } = dialog
    resetMut.mutate(user.id, {
      onSuccess: (res) =>
        setDialog({
          kind: 'reveal',
          title: t('users.reveal.reset.title', { username: user.username }),
          message: t('users.reveal.reset.message'),
          secret: res.temp_password,
        }),
      onError: (e) => toast.error(apiErrorMessage(e, t)),
    })
  }

  const toggleActive = (user: User) =>
    updateMut.mutate(
      { id: user.id, body: { is_active: !user.is_active } },
      { onError: (e) => toast.error(apiErrorMessage(e, t)) },
    )

  return (
    <div className="animate-fade-in space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">{t('users.title')}</h1>
          <p className="mt-1 text-[13px] text-ink-muted">{t('users.subtitle')}</p>
        </div>
        <Button onClick={() => setDialog({ kind: 'create' })} className="shrink-0">
          <Plus className="h-5 w-5 shrink-0" />
          {t('users.new')}
        </Button>
      </header>

      <UserList
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error ? apiErrorMessage(error, t) : undefined}
        onRetry={() => void refetch()}
        onCreate={() => setDialog({ kind: 'create' })}
        onEdit={(user) => setDialog({ kind: 'edit', user })}
        onDelete={(user) => setDialog({ kind: 'delete', user })}
        onKey={(user) => setDialog({ kind: 'key', user })}
        onPermissions={(user) => setDialog({ kind: 'permissions', user })}
        onReset={(user) => setDialog({ kind: 'reset', user })}
        onToggleActive={toggleActive}
      />

      <UserFormModal
        open={dialog.kind === 'create' || dialog.kind === 'edit'}
        user={dialog.kind === 'edit' ? dialog.user : undefined}
        submitting={createMut.isPending || updateMut.isPending}
        onSubmit={handleSubmit}
        onClose={close}
      />

      <UserKeyModal
        open={dialog.kind === 'key'}
        user={dialog.kind === 'key' ? dialog.user : null}
        onClose={close}
      />

      <PermissionsModal
        open={dialog.kind === 'permissions'}
        user={dialog.kind === 'permissions' ? dialog.user : null}
        onClose={close}
      />

      <ConfirmDialog
        open={dialog.kind === 'delete'}
        title={t('users.delete.title')}
        message={
          dialog.kind === 'delete'
            ? t('users.delete.message', { username: dialog.user.username })
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={close}
      />

      <ConfirmDialog
        open={dialog.kind === 'reset'}
        title={t('users.reset.title')}
        message={
          dialog.kind === 'reset'
            ? t('users.reset.message', { username: dialog.user.username })
            : ''
        }
        confirmLabel={t('users.reset.title')}
        loading={resetMut.isPending}
        onConfirm={handleReset}
        onClose={close}
      />

      <Modal open={dialog.kind === 'reveal'} onClose={close} title={dialog.kind === 'reveal' ? dialog.title : ''}>
        {dialog.kind === 'reveal' && (
          <RevealOncePanel secret={dialog.secret} message={dialog.message} onDone={close} />
        )}
      </Modal>
    </div>
  )
}

/** Render the users table across its loading / error / empty / data states. */
function UserList({
  data,
  isLoading,
  isError,
  message,
  onRetry,
  onCreate,
  onEdit,
  onDelete,
  onKey,
  onPermissions,
  onReset,
  onToggleActive,
}: {
  data: User[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  onCreate: () => void
  onEdit: (user: User) => void
  onDelete: (user: User) => void
  onKey: (user: User) => void
  onPermissions: (user: User) => void
  onReset: (user: User) => void
  onToggleActive: (user: User) => void
}) {
  const { t } = useTranslation()
  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-16 rounded-card" />
        ))}
      </div>
    )
  }
  if (isError) {
    return <QueryError title={t('users.error')} message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<UsersIcon className="h-7 w-7" />}
        title={t('users.empty.title')}
        description={t('users.empty.description')}
        action={<Button onClick={onCreate}>{t('users.new')}</Button>}
      />
    )
  }
  return (
    <div className="divide-y divide-border rounded-card border border-border">
      {data.map((user) => (
        <UserRow
          key={user.id}
          user={user}
          onEdit={onEdit}
          onDelete={onDelete}
          onKey={onKey}
          onPermissions={onPermissions}
          onReset={onReset}
          onToggleActive={onToggleActive}
        />
      ))}
    </div>
  )
}

/** A single user row: identity + role, status toggle, and inline actions. */
function UserRow({
  user,
  onEdit,
  onDelete,
  onKey,
  onPermissions,
  onReset,
  onToggleActive,
}: {
  user: User
  onEdit: (user: User) => void
  onDelete: (user: User) => void
  onKey: (user: User) => void
  onPermissions: (user: User) => void
  onReset: (user: User) => void
  onToggleActive: (user: User) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-ink">{user.username}</span>
          {user.is_admin && <Badge>{t('users.admin')}</Badge>}
          {user.name && <span className="truncate text-xs text-ink-muted">{user.name}</span>}
        </div>
        <p className="mt-0.5 truncate text-xs text-ink-faint">
          {user.email ? `${user.email} · ` : ''}
          {user.api_key
            ? t('users.row.key', { prefix: user.api_key.key_prefix })
            : t('users.row.noKey')}{' '}
          · {t('users.row.created', { date: formatDate(user.created_at) })}
          {user.rate_limit_rpm > 0 && ` · ${t('users.row.rpm', { rpm: user.rate_limit_rpm })}`}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          onClick={() => onToggleActive(user)}
          aria-label={user.is_active ? t('users.row.deactivateAria') : t('users.row.activateAria')}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium transition-colors hover:border-ink-faint"
        >
          <span className={cn('h-1.5 w-1.5 rounded-full', user.is_active ? 'bg-ok' : 'bg-ink-faint')} />
          <span className={user.is_active ? 'text-ok' : 'text-ink-muted'}>
            {user.is_active ? t('users.status.active') : t('users.status.inactive')}
          </span>
        </button>
        <DropdownMenu triggerAriaLabel={t('users.row.manageAria', { username: user.username })}>
          <DropdownItem icon={<ShieldCheck className="h-4 w-4" />} onSelect={() => onPermissions(user)}>
            {t('users.actions.permissions')}
          </DropdownItem>
          <DropdownItem icon={<KeyRound className="h-4 w-4" />} onSelect={() => onKey(user)}>
            {t('users.actions.apiKey')}
          </DropdownItem>
          <DropdownItem icon={<RotateCcw className="h-4 w-4" />} onSelect={() => onReset(user)}>
            {t('users.reset.title')}
          </DropdownItem>
          <DropdownItem icon={<Pencil className="h-4 w-4" />} onSelect={() => onEdit(user)}>
            {t('users.actions.edit')}
          </DropdownItem>
          <DropdownItem icon={<Trash2 className="h-4 w-4" />} danger onSelect={() => onDelete(user)}>
            {t('users.actions.delete')}
          </DropdownItem>
        </DropdownMenu>
      </div>
    </div>
  )
}
