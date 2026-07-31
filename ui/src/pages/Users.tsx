import { useState } from 'react'
import { KeyRound, Pencil, Plus, RotateCcw, ShieldCheck, Trash2, Users as UsersIcon } from 'lucide-react'
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
            title: `User “${user.username}” created`,
            message:
              'Share this one-time password with the user — they will be asked to change it on first sign-in. It is shown only once.',
            secret: user.temp_password,
          }),
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
        toast.success(`User “${user.username}” deleted.`)
        close()
      },
      onError: (e) => toast.error(e.message),
    })
  }

  const handleReset = () => {
    if (dialog.kind !== 'reset') return
    const { user } = dialog
    resetMut.mutate(user.id, {
      onSuccess: (res) =>
        setDialog({
          kind: 'reveal',
          title: `Password reset for “${user.username}”`,
          message:
            'Share this one-time password with the user — their old password no longer works and they will set a new one on next sign-in. Shown only once.',
          secret: res.temp_password,
        }),
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
            Each user signs in with a username + password (admins get the full console; others are
            scoped by their grants) and may hold one API key for MCP/programmatic access.
          </p>
        </div>
        <Button onClick={() => setDialog({ kind: 'create' })} className="shrink-0">
          <Plus className="h-5 w-5 shrink-0" />
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
        title="Delete user"
        message={
          dialog.kind === 'delete'
            ? `Delete “${dialog.user.username}”? Their API key and all permission grants are permanently removed. This cannot be undone.`
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={close}
      />

      <ConfirmDialog
        open={dialog.kind === 'reset'}
        title="Reset password"
        message={
          dialog.kind === 'reset'
            ? `Reset the password for “${dialog.user.username}”? Their current password stops working immediately and any active session is signed out.`
            : ''
        }
        confirmLabel="Reset password"
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
        description="Create a user, share their one-time password, and grant access to workspaces or collections."
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
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-ink">{user.username}</span>
          {user.is_admin && <Badge>Admin</Badge>}
          {user.name && <span className="truncate text-xs text-ink-muted">{user.name}</span>}
        </div>
        <p className="mt-0.5 truncate text-xs text-ink-faint">
          {user.email ? `${user.email} · ` : ''}
          {user.api_key ? `key ${user.api_key.key_prefix}…` : 'no API key'} · created{' '}
          {formatDate(user.created_at)}
          {user.rate_limit_rpm > 0 && ` · ${user.rate_limit_rpm} rpm`}
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
        <DropdownMenu triggerAriaLabel={`Manage ${user.username}`}>
          <DropdownItem icon={<ShieldCheck className="h-4 w-4" />} onSelect={() => onPermissions(user)}>
            Permissions
          </DropdownItem>
          <DropdownItem icon={<KeyRound className="h-4 w-4" />} onSelect={() => onKey(user)}>
            API key
          </DropdownItem>
          <DropdownItem icon={<RotateCcw className="h-4 w-4" />} onSelect={() => onReset(user)}>
            Reset password
          </DropdownItem>
          <DropdownItem icon={<Pencil className="h-4 w-4" />} onSelect={() => onEdit(user)}>
            Edit
          </DropdownItem>
          <DropdownItem icon={<Trash2 className="h-4 w-4" />} danger onSelect={() => onDelete(user)}>
            Delete
          </DropdownItem>
        </DropdownMenu>
      </div>
    </div>
  )
}
