import { useEffect, useState } from 'react'
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
      title={editing ? 'Edit workspace' : 'New workspace'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} loading={submitting} disabled={!name}>
            {editing ? 'Save changes' : 'Create'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" htmlFor="ws-name">
          <Input
            id="ws-name"
            autoFocus
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder="e.g. Research"
          />
        </Field>
        <Field label="Description" htmlFor="ws-desc" hint="Optional — what lives in this workspace.">
          <Textarea
            id="ws-desc"
            value={values.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder="Optional"
          />
        </Field>
        <Field label="Color">
          <ColorPicker value={values.color} onChange={(c) => set('color', c)} />
        </Field>
        <Field label="Icon">
          <IconPicker value={values.icon} onChange={(i) => set('icon', i)} />
        </Field>
      </div>
    </Modal>
  )
}
