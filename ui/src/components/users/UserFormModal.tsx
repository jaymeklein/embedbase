import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { User } from '../../api/types'
import { Button, Field, Input, Modal } from '../ui'

/** The editable surface of a user — what create and edit both collect. */
export interface UserFormValues {
  username: string
  email: string
  name: string
  is_active: boolean
  is_admin: boolean
  rate_limit_rpm: number
}

const DEFAULTS: UserFormValues = {
  username: '',
  email: '',
  name: '',
  is_active: true,
  is_admin: false,
  rate_limit_rpm: 0,
}

/** Shape checks for immediate feedback; the API re-validates authoritatively. */
const isValidEmail = (email: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
const isValidUsername = (u: string) => /^[a-zA-Z0-9._+@-]{3,64}$/.test(u)

function valuesFrom(user: User | undefined): UserFormValues {
  if (!user) return DEFAULTS
  return {
    username: user.username,
    email: user.email ?? '',
    name: user.name ?? '',
    is_active: user.is_active,
    is_admin: user.is_admin,
    rate_limit_rpm: user.rate_limit_rpm ?? 0,
  }
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
  const { t } = useTranslation()
  const [values, setValues] = useState<UserFormValues>(DEFAULTS)

  // Reseed every time the modal opens so stale edits never leak between rows.
  useEffect(() => {
    if (open) setValues(valuesFrom(user))
  }, [open, user])

  const editing = Boolean(user)
  const email = values.email.trim()
  const username = values.username.trim()
  // Email is optional — blank is valid; a non-blank value must be a real address.
  const emailValid = email === '' || isValidEmail(email)
  const usernameValid = isValidUsername(username)
  const valid = emailValid && usernameValid
  const set = <K extends keyof UserFormValues>(key: K, value: UserFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const submit = () => {
    if (!valid) return
    onSubmit({ ...values, username, email, name: values.name.trim() })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? t('users.editTitle') : t('users.new')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} loading={submitting} disabled={!valid}>
            {editing ? t('common.saveChanges') : t('common.create')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field
          label={t('auth.field.username')}
          htmlFor="user-username"
          hint={t('users.form.usernameHint')}
          error={username && !usernameValid ? t('users.form.usernameError') : undefined}
        >
          <Input
            id="user-username"
            autoFocus
            value={values.username}
            onChange={(e) => set('username', e.target.value)}
            placeholder={t('users.form.usernamePlaceholder')}
          />
        </Field>
        <Field
          label={t('users.form.email')}
          htmlFor="user-email"
          hint={t('users.form.emailHint')}
          error={email && !emailValid ? t('users.form.emailError') : undefined}
        >
          <Input
            id="user-email"
            type="email"
            value={values.email}
            onChange={(e) => set('email', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder={t('users.form.emailPlaceholder')}
          />
        </Field>
        <Field label={t('common.name')} htmlFor="user-name" hint={t('users.form.nameHint')}>
          <Input
            id="user-name"
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder={t('common.optional')}
          />
        </Field>
        <Field
          label={t('users.form.rateLimit')}
          htmlFor="user-rate-limit"
          hint={t('users.form.rateLimitHint')}
        >
          <Input
            id="user-rate-limit"
            type="number"
            min={0}
            step={1}
            value={values.rate_limit_rpm}
            onChange={(e) =>
              set('rate_limit_rpm', Math.max(0, Math.floor(Number(e.target.value) || 0)))
            }
          />
        </Field>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={values.is_active}
            onChange={(e) => set('is_active', e.target.checked)}
            className="h-4 w-4 rounded border-border accent-accent"
          />
          {t('users.form.activeLabel')}
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={values.is_admin}
            onChange={(e) => set('is_admin', e.target.checked)}
            className="h-4 w-4 rounded border-border accent-accent"
          />
          {t('users.form.adminLabel')}
        </label>
      </div>
    </Modal>
  )
}
