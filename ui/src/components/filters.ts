/** Shared building blocks for the documents and ingestion-queue filter bars, so the option lists,
 *  input styling, and text normalisation live in one place instead of being copied per page.
 *  (The two bars keep their own field layouts; only these shared literals/helpers are hoisted.) */

/** Shared styling for filter inputs and selects. */
export const filterInputCls =
  'h-9 rounded-control border border-border bg-canvas px-2.5 text-[13px] text-ink ' +
  'placeholder:text-ink-faint focus:border-accent focus:outline-none'

/** Every extension that can appear as a stored `file_type` — the core formats plus the docling
 *  office formats (present only when that backend is on). Mirrors the parser registry in
 *  api/adapters/parsers/__init__.py, so the filter offers exactly what can be ingested. */
export const FILE_TYPES = ['.pdf', '.txt', '.md', '.markdown', '.docx', '.pptx'] as const

/** Ingestion statuses, for the status filter dropdowns (documents show the latest; the queue the
 *  per-attempt status). */
export const INGEST_STATUSES = ['pending', 'processing', 'done', 'failed', 'rate_limited'] as const

/** Trim a text-filter value to a non-empty string, or undefined (empty → filter cleared). */
export const filterText = (s: string): string | undefined => (s.trim() === '' ? undefined : s.trim())
