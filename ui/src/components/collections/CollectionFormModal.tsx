import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Collection } from '../../api/types'
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

/** The editable surface of a collection — what create and edit both collect. */
export interface CollectionFormValues {
  name: string
  description: string
  color: string
  icon: string
}

const DEFAULTS: CollectionFormValues = {
  name: '',
  description: '',
  color: SWATCHES[6],
  icon: 'database',
}

function valuesFrom(collection: Collection | undefined): CollectionFormValues {
  if (!collection) return DEFAULTS
  return {
    name: collection.name,
    description: collection.description ?? '',
    color: collection.color || DEFAULTS.color,
    icon: collection.icon || DEFAULTS.icon,
  }
}

/**
 * Create / edit modal for a collection. Presentational: it owns the form state
 * but delegates the write to `onSubmit`, so the page keeps the mutation + toast.
 * `collection` undefined → create; otherwise edit, seeded from its fields.
 */
export function CollectionFormModal({
  open,
  collection,
  submitting,
  onSubmit,
  onClose,
}: {
  open: boolean
  collection?: Collection
  submitting: boolean
  onSubmit: (values: CollectionFormValues) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<CollectionFormValues>(DEFAULTS)

  // Reseed every time the modal opens so stale edits never leak between rows.
  useEffect(() => {
    if (open) setValues(valuesFrom(collection))
  }, [open, collection])

  const editing = Boolean(collection)
  const name = values.name.trim()
  const set = <K extends keyof CollectionFormValues>(key: K, value: CollectionFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const submit = () => {
    if (!name) return
    onSubmit({ ...values, name })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? t('collections.editTitle') : t('collections.new')}
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
        <Field label={t('common.name')} htmlFor="col-name">
          <Input
            id="col-name"
            autoFocus
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder={t('collections.namePlaceholder')}
          />
        </Field>
        <Field label={t('common.description')} htmlFor="col-desc" hint={t('collections.descHint')}>
          <Textarea
            id="col-desc"
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
