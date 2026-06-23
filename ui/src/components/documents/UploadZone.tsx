import { useRef, useState, type DragEvent } from 'react'
import { UploadCloud } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Spinner } from '../ui'

/** Extensions the ingestion pipeline accepts (PDF/text/code + docling office). */
const ACCEPT =
  '.pdf,.txt,.md,.csv,.json,.docx,.pptx,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.rb,.c,.cpp,.h'

/**
 * Drag-and-drop upload surface with a file-picker fallback. Purely presentational:
 * it collects File objects and hands them to `onFiles`; size/type validation and
 * the actual upload live in the page.
 */
export function UploadZone({
  onFiles,
  busy,
  maxSizeMb,
}: {
  onFiles: (files: File[]) => void
  busy: boolean
  maxSizeMb: number
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const emit = (list: FileList | null) => {
    if (list && list.length > 0) onFiles(Array.from(list))
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    if (!busy) emit(e.dataTransfer.files)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-card border border-dashed px-6 py-10 text-center transition-colors',
        dragging ? 'border-accent bg-accent-weak' : 'border-border bg-surface',
        busy && 'opacity-60',
      )}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          emit(e.target.files)
          e.target.value = ''
        }}
      />
      {busy ? (
        <Spinner className="h-7 w-7 text-accent" />
      ) : (
        <UploadCloud className="h-7 w-7 text-ink-faint" />
      )}
      <p className="text-[13px] text-ink">
        {busy ? 'Uploading…' : 'Drag files here, or '}
        {!busy && (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="font-medium text-accent hover:underline"
          >
            browse
          </button>
        )}
      </p>
      <p className="text-xs text-ink-faint">
        PDF, text, Markdown, code, CSV, JSON, DOCX, PPTX · up to {maxSizeMb} MB each
      </p>
    </div>
  )
}
