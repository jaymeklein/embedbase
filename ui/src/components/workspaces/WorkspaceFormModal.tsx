import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Workspace } from '../../api/types'
import {
  Button,
  ColorPicker,
  Field,
  IconPicker,
  Input,
  Modal,
  SWATCHES,
  Textarea,
} from '../ui'

/** The editable surface of a workspace — what create and edit both collect. */
export interface WorkspaceFormValues {
  name: string
  description: string
  color: string
  icon: string
}

const DEFAULTS: WorkspaceFormValues = {
  name: '',
  description: '',
  color: SWATCHES[1],
  icon: 'folder',
}

function valuesFrom(workspace: Workspace | undefined): WorkspaceFormValues {
  if (!workspace) return DEFAULTS
  return {
    name: workspace.name,
    description: workspace.description ?? '',
    color: workspace.color || DEFAULTS.color,
    icon: workspace.icon || DEFAULTS.icon,
  }
}

/**
 * Create / edit modal for a workspace. Presentational: it owns the form state
 * but delegates the write to `onSubmit`, so the page keeps the mutation + toast.
 * `workspace` undefined → create; otherwise edit, seeded from its fields.
 */
export function WorkspaceFormModal({
  open,
  workspace,
  submitting,
  onSubmit,
  onClose,
}: {
  open: boolean
  workspace?: Workspace
  submitting: boolean
  onSubmit: (values: WorkspaceFormValues) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<WorkspaceFormValues>(DEFAULTS)

  // Reseed every time the modal opens so stale edits never leak between rows.
  useEffect(() => {
    if (open) setValues(valuesFrom(workspace))
  }, [open, workspace])

  const editing = Boolean(workspace)
  const name = values.name.trim()
  const set = <K extends keyof WorkspaceFormValues>(key: K, value: WorkspaceFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const submit = () => {
    if (!name) return
    onSubmit({ ...values, name })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? t('workspaces.editTitle') : t('workspaces.new')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} loading={submitting} disabled={!name}>
            {editing ? t('common.saveChanges') : t('common.create')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t('common.name')} htmlFor="ws-name">
          <Input
            id="ws-name"
            autoFocus
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder={t('workspaces.namePlaceholder')}
          />
        </Field>
        <Field label={t('common.description')} htmlFor="ws-desc" hint={t('workspaces.descHint')}>
          <Textarea
            id="ws-desc"
            value={values.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder={t('common.optional')}
          />
        </Field>
        <Field label={t('common.color')}>
          <ColorPicker value={values.color} onChange={(c) => set('color', c)} />
        </Field>
        <Field label={t('common.icon')}>
          <IconPicker value={values.icon} onChange={(i) => set('icon', i)} />
        </Field>
      </div>
    </Modal>
  )
}
