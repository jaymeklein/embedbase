import { useState } from 'react'
import { Search, SlidersHorizontal, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  FILE_TYPES,
  filterInputCls as inputCls,
  filterText as text,
  INGEST_STATUSES as STATUSES,
} from '../filters'

/** The filter fields the bar edits — the server-side `DocumentQuery` minus pagination and tags
 *  (tags have their own picker). The backend also accepts `updated_after`/`updated_before`; only
 *  the created range is surfaced here. */
export interface DocumentFilterValues {
  filename?: string
  status?: string
  file_type?: string
  indexed?: boolean
  storage_backend?: string
  embedding_model?: string
  min_size?: number
  max_size?: number
  created_after?: string
  created_before?: string
}

/** Comprehensive, collapsible filter bar for the documents listing. Primary filters (search,
 *  status, type, indexed) stay visible; the rest sit under “More”. Emits the whole value object
 *  on every change so the parent can reset pagination to the first page. */
export function DocumentFilters({
  value,
  onChange,
}: {
  value: DocumentFilterValues
  onChange: (next: DocumentFilterValues) => void
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const set = <K extends keyof DocumentFilterValues>(key: K, v: DocumentFilterValues[K]) =>
    onChange({ ...value, [key]: v })
  const num = (s: string) => (s === '' ? undefined : Number(s))
  const active = Object.values(value).some((v) => v !== undefined && v !== '')

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
          <input
            className={`${inputCls} w-full pl-8`}
            placeholder={t('documents.filters.searchPlaceholder')}
            value={value.filename ?? ''}
            onChange={(e) => set('filename', text(e.target.value))}
          />
        </div>
        <select
          className={inputCls}
          aria-label={t('documents.filters.statusAria')}
          value={value.status ?? ''}
          onChange={(e) => set('status', text(e.target.value))}
        >
          <option value="">{t('documents.filters.anyStatus')}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {t(`status.${s}`)}
            </option>
          ))}
        </select>
        <select
          className={inputCls}
          aria-label={t('documents.filters.typeAria')}
          value={value.file_type ?? ''}
          onChange={(e) => set('file_type', text(e.target.value))}
        >
          <option value="">{t('documents.filters.anyType')}</option>
          {FILE_TYPES.map((ft) => (
            <option key={ft} value={ft}>
              {ft}
            </option>
          ))}
        </select>
        <select
          className={inputCls}
          aria-label={t('documents.filters.indexedAria')}
          value={value.indexed === undefined ? '' : String(value.indexed)}
          onChange={(e) =>
            set('indexed', e.target.value === '' ? undefined : e.target.value === 'true')
          }
        >
          <option value="">{t('documents.filters.indexedAny')}</option>
          <option value="true">{t('documents.filters.indexed')}</option>
          <option value="false">{t('documents.filters.notIndexed')}</option>
        </select>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={`inline-flex h-9 items-center gap-1.5 rounded-control border px-2.5 text-[13px] transition-colors ${
            expanded ? 'border-accent text-ink' : 'border-border text-ink-muted hover:text-ink'
          }`}
        >
          <SlidersHorizontal className="h-4 w-4" />
          {t('common.more')}
        </button>
        {active && (
          <button
            type="button"
            onClick={() => onChange({})}
            className="inline-flex h-9 items-center gap-1 rounded-control px-2 text-[13px] text-ink-muted hover:text-ink"
          >
            <X className="h-4 w-4" />
            {t('common.clear')}
          </button>
        )}
      </div>

      {expanded && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-control border border-border/60 bg-canvas/50 p-2.5">
          <input
            className={inputCls}
            placeholder={t('documents.filters.storageBackend')}
            value={value.storage_backend ?? ''}
            onChange={(e) => set('storage_backend', text(e.target.value))}
          />
          <input
            className={inputCls}
            placeholder={t('documents.filters.embeddingModel')}
            value={value.embedding_model ?? ''}
            onChange={(e) => set('embedding_model', text(e.target.value))}
          />
          <label className="flex items-center gap-1.5 text-xs text-ink-muted">
            {t('documents.filters.size')}
            <input
              type="number"
              min={0}
              className={`${inputCls} w-24`}
              placeholder={t('common.min')}
              value={value.min_size ?? ''}
              onChange={(e) => set('min_size', num(e.target.value))}
            />
            <span className="text-ink-faint">–</span>
            <input
              type="number"
              min={0}
              className={`${inputCls} w-24`}
              placeholder={t('common.max')}
              value={value.max_size ?? ''}
              onChange={(e) => set('max_size', num(e.target.value))}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-ink-muted">
            {t('documents.filters.created')}
            <input
              type="date"
              className={`${inputCls} w-40`}
              value={value.created_after ?? ''}
              onChange={(e) => set('created_after', text(e.target.value))}
            />
            <span className="text-ink-faint">–</span>
            <input
              type="date"
              className={`${inputCls} w-40`}
              value={value.created_before ?? ''}
              onChange={(e) => set('created_before', text(e.target.value))}
            />
          </label>
        </div>
      )}
    </div>
  )
}
