import { useState } from 'react'
import { KeyRound, Pencil, Plus, ShieldCheck, Trash2, Users as UsersIcon } from 'lucide-react'
import { useCreateUser, useDeleteUser, useUpdateUser, useUsers } from '../api/hooks'
import type { User, UserUpdate } from '../api/types'
import { Button, ConfirmDialog, EmptyState, QueryError, Skeleton, useToast } from '../components/ui'
import { UserFormModal, type UserFormValues } from '../components/users/UserFormModal'
import { UserKeyModal } from '../components/users/UserKeyModal'
import { PermissionsModal } from '../components/users/PermissionsModal'
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

/** Reduce a full form submission to only the fields that actually changed. */
function changedFields(user: User, values: UserFormValues): UserUpdate {
  const body: UserUpdate = {}
  if (values.email !== user.email) body.email = values.email
  if (values.name !== (user.name ?? '')) body.name = values.name
  if (values.is_active !== user.is_active) body.is_active = values.is_active
  return body
}

/** Users admin: list, create, edit, activate/deactivate, assign a key, and grant permissions. */
export default function Users() {
  const { data, isLoading, isError, error, refetch } = useUsers()
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' })
  const close = () => setDialog({ kind: 'none' })
  const toast = useToast()
  const createMut = useCreateUser()
  const updateMut = useUpdateUser()
  const deleteMut = useDeleteUser()

  const handleSubmit = (values: UserFormValues) => {
    if (dialog.kind === 'create') {
      createMut.mutate(values, {
        onSuccess: () => {
          toast.success(`User “${values.email}” created.`)
          close()
        },
        onError: (e) => toast.error(e.message),
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
            toast.success('User updated.')
            close()
          },
          onError: (e) => toast.error(e.message),
        },
      )
    }
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { user } = dialog
    deleteMut.mutate(user.id, {
      onSuccess: () => {
        toast.success(`User “${user.email}” deleted.`)
        close()
      },
      onError: (e) => toast.error(e.message),
    })
  }

  const toggleActive = (user: User) =>
    updateMut.mutate(
      { id: user.id, body: { is_active: !user.is_active } },
      { onError: (e) => toast.error(e.message) },
    )

  return (
    <div className="animate-fade-in space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Users</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Each user has one API key; access is scoped by permission grants. The MCP server and REST
            API enforce those grants — an inactive user's key stops working.
          </p>
        </div>
        <Button onClick={() => setDialog({ kind: 'create' })}>
          <Plus className="h-5 w-5" />
          New user
        </Button>
      </header>

      <UserList
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
        onRetry={() => void refetch()}
        onCreate={() => setDialog({ kind: 'create' })}
        onEdit={(user) => setDialog({ kind: 'edit', user })}
        onDelete={(user) => setDialog({ kind: 'delete', user })}
        onKey={(user) => setDialog({ kind: 'key', user })}
        onPermissions={(user) => setDialog({ kind: 'permissions', user })}
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
        title="Delete user"
        message={
          dialog.kind === 'delete'
            ? `Delete “${dialog.user.email}”? Their API key and all permission grants are permanently removed. This cannot be undone.`
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={close}
      />
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
  onToggleActive: (user: User) => void
}) {
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
    return <QueryError title="Could not load users" message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<UsersIcon className="h-7 w-7" />}
        title="No users yet"
        description="Create a user, assign its API key, and grant access to workspaces or collections."
        action={<Button onClick={onCreate}>New user</Button>}
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
          onToggleActive={onToggleActive}
        />
      ))}
    </div>
  )
}

/** A single user row: identity, status toggle, key state, and inline actions. */
function UserRow({
  user,
  onEdit,
  onDelete,
  onKey,
  onPermissions,
  onToggleActive,
}: {
  user: User
  onEdit: (user: User) => void
  onDelete: (user: User) => void
  onKey: (user: User) => void
  onPermissions: (user: User) => void
  onToggleActive: (user: User) => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-ink">{user.name || user.email}</span>
          {user.name && <span className="truncate text-xs text-ink-muted">{user.email}</span>}
        </div>
        <p className="mt-0.5 text-xs text-ink-faint">
          {user.api_key ? `Key ${user.api_key.key_prefix}…` : 'No API key'} · created{' '}
          {formatDate(user.created_at)}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          onClick={() => onToggleActive(user)}
          aria-label={user.is_active ? 'Deactivate user' : 'Activate user'}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium transition-colors hover:border-ink-faint"
        >
          <span className={cn('h-1.5 w-1.5 rounded-full', user.is_active ? 'bg-ok' : 'bg-ink-faint')} />
          <span className={user.is_active ? 'text-ok' : 'text-ink-muted'}>
            {user.is_active ? 'Active' : 'Inactive'}
          </span>
        </button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Permissions for ${user.email}`}
          onClick={() => onPermissions(user)}
          className="h-9 w-9 px-0"
        >
          <ShieldCheck className="h-5 w-5" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`API key for ${user.email}`}
          onClick={() => onKey(user)}
          className="h-9 w-9 px-0"
        >
          <KeyRound className="h-5 w-5" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Edit ${user.email}`}
          onClick={() => onEdit(user)}
          className="h-9 w-9 px-0"
        >
          <Pencil className="h-5 w-5" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Delete ${user.email}`}
          onClick={() => onDelete(user)}
          className="h-9 w-9 px-0 hover:text-err"
        >
          <Trash2 className="h-5 w-5" />
        </Button>
      </div>
    </div>
  )
}
