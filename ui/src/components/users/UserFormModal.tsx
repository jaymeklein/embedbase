import { useEffect, useState } from 'react'
import type { User } from '../../api/types'
import { Button, Field, Input, Modal } from '../ui'

/** The editable surface of a user — what create and edit both collect. */
export interface UserFormValues {
  email: string
  name: string
  is_active: boolean
}

const DEFAULTS: UserFormValues = { email: '', name: '', is_active: true }

function valuesFrom(user: User | undefined): UserFormValues {
  if (!user) return DEFAULTS
  return { email: user.email, name: user.name ?? '', is_active: user.is_active }
}

/**
 * Create / edit modal for a user. Presentational: owns the form state but
 * delegates the write to `onSubmit`, so the page keeps the mutation + toast.
 * `user` undefined → create; otherwise edit, seeded from its fields.
 */
export function UserFormModal({
  open,
  user,
  submitting,
  onSubmit,
  onClose,
}: {
  open: boolean
  user?: User
  submitting: boolean
  onSubmit: (values: UserFormValues) => void
  onClose: () => void
}) {
  const [values, setValues] = useState<UserFormValues>(DEFAULTS)

  // Reseed every time the modal opens so stale edits never leak between rows.
  useEffect(() => {
    if (open) setValues(valuesFrom(user))
  }, [open, user])

  const editing = Boolean(user)
  const email = values.email.trim()
  const set = <K extends keyof UserFormValues>(key: K, value: UserFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const submit = () => {
    if (!email) return
    onSubmit({ ...values, email, name: values.name.trim() })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? 'Edit user' : 'New user'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} loading={submitting} disabled={!email}>
            {editing ? 'Save changes' : 'Create'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Email" htmlFor="user-email">
          <Input
            id="user-email"
            autoFocus
            type="email"
            value={values.email}
            onChange={(e) => set('email', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder="e.g. jane@example.com"
          />
        </Field>
        <Field label="Name" htmlFor="user-name" hint="Optional — a display name.">
          <Input
            id="user-name"
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="Optional"
          />
        </Field>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={values.is_active}
            onChange={(e) => set('is_active', e.target.checked)}
            className="h-4 w-4 rounded border-border accent-accent"
          />
          Active — an inactive user's API key stops working
        </label>
      </div>
    </Modal>
  )
}
