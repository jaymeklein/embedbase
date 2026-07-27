/**
 * Typed, authenticated API client for the EmbedBase backend.
 *
 * Every request injects the active bearer credential (a login session JWT, else
 * the master key — see `tokenStore.getToken`) as `Authorization: Bearer <token>`.
 * A dead credential — any 401, or the 403 a deactivated user gets — triggers
 * `notifyUnauthorized()` so the app signs out and returns to the login screen,
 * then throws an {@link ApiError} carrying the status code.
 */

import { getToken, notifyUnauthorized } from './tokenStore'
import { saveBlob } from '../lib/download'
import type {
  Accelerator,
  AppConfig,
  Collection,
  CollectionCreate,
  CollectionUpdate,
  CreatedUser,
  DocumentListResponse,
  DocumentQuery,
  GrantCreate,
  Health,
  IndexEnqueueResponse,
  IndexStatusResponse,
  JobListResponse,
  JobQuery,
  JobStats,
  JobStatus,
  McpToolCatalog,
  MintedUserKey,
  Permission,
  ResetPasswordResponse,
  SessionResponse,
  GraphResponse,
  SearchRequest,
  SearchResponse,
  Tag,
  TagCreate,
  TagItems,
  TagMerge,
  TagUpdate,
  UploadAccepted,
  User,
  UserCreate,
  UserKeyCreate,
  UserUpdate,
  Workspace,
  WorkspaceCreate,
  WorkspaceDetail,
  WorkspaceUpdate,
} from './types'

const BASE = '/api'

/** Error carrying the HTTP status so callers can branch on `401` etc. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface RequestOptions {
  method?: string
  /** JSON-serialisable body, or a `FormData` for multipart uploads. */
  body?: unknown
  signal?: AbortSignal
}

/** Build headers with auth + the right content-type for the body kind. */
function buildHeaders(body: unknown): { headers: Headers; payload: BodyInit | undefined } {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  if (body instanceof FormData) {
    // Let the browser set `multipart/form-data` + boundary — never force JSON.
    return { headers, payload: body }
  }
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json')
    return { headers, payload: JSON.stringify(body) }
  }
  return { headers, payload: undefined }
}

/** Extract FastAPI's `{detail}` message, falling back to a status string. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json()
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
    }
  } catch {
    // Non-JSON body — fall through to the generic message.
  }
  return `Request failed (HTTP ${res.status})`
}

/**
 * Throw for a failed response — signing the operator out first when the credential
 * itself is dead. That's any 401 (missing/expired/invalid session or key) and the
 * 403 a deactivated user gets (`"User is inactive"` — the coarse-auth convention of
 * `api/services/auth.py`). Staying "signed in" would only error every subsequent
 * request, so both clear the credentials and return the app to the login screen.
 */
async function raiseForStatus(res: Response): Promise<void> {
  if (res.ok) return
  if (res.status === 401) {
    notifyUnauthorized()
    throw new ApiError(401, 'Session expired. Please sign in again.')
  }
  const message = await errorMessage(res)
  if (res.status === 403 && message === 'User is inactive') {
    notifyUnauthorized()
    throw new ApiError(403, 'Your account has been deactivated.')
  }
  throw new ApiError(res.status, message)
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options
  const { headers, payload } = buildHeaders(body)

  const res = await fetch(`${BASE}${path}`, { method, headers, body: payload, signal })

  await raiseForStatus(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const enc = encodeURIComponent

/** Serialise a query object to a `?a=b&c=d` string: skips null/empty values and expands array
 *  params (e.g. repeated `tag`). Returns '' when nothing is set. Shared by the documents and
 *  ingestion-jobs listings. */
function toQueryString(query: object): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === '') continue
    if (Array.isArray(value)) value.forEach((v) => params.append(key, String(v)))
    else params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const api = {
  // ── Workspaces ────────────────────────────────────────────────────────────
  listWorkspaces: () => request<Workspace[]>('/workspaces'),
  createWorkspace: (body: WorkspaceCreate) =>
    request<Workspace>('/workspaces', { method: 'POST', body }),
  getWorkspace: (id: string) => request<WorkspaceDetail>(`/workspaces/${enc(id)}`),
  updateWorkspace: (id: string, body: WorkspaceUpdate) =>
    request<Workspace>(`/workspaces/${enc(id)}`, { method: 'PATCH', body }),
  deleteWorkspace: (id: string) =>
    request<void>(`/workspaces/${enc(id)}`, { method: 'DELETE' }),

  // ── Collections ───────────────────────────────────────────────────────────
  listCollections: (wsId: string) =>
    request<Collection[]>(`/workspaces/${enc(wsId)}/collections`),
  createCollection: (wsId: string, body: CollectionCreate) =>
    request<Collection>(`/workspaces/${enc(wsId)}/collections`, { method: 'POST', body }),
  getCollection: (wsId: string, colId: string) =>
    request<Collection>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}`),
  updateCollection: (wsId: string, colId: string, body: CollectionUpdate) =>
    request<Collection>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}`, {
      method: 'PATCH',
      body,
    }),
  deleteCollection: (wsId: string, colId: string) =>
    request<void>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}`, { method: 'DELETE' }),

  // ── Tags ──────────────────────────────────────────────────────────────────
  listTags: (wsId: string) => request<Tag[]>(`/workspaces/${enc(wsId)}/tags`),
  createTag: (wsId: string, body: TagCreate) =>
    request<Tag>(`/workspaces/${enc(wsId)}/tags`, { method: 'POST', body }),
  updateTag: (wsId: string, tagId: string, body: TagUpdate) =>
    request<Tag>(`/workspaces/${enc(wsId)}/tags/${enc(tagId)}`, { method: 'PATCH', body }),
  deleteTag: (wsId: string, tagId: string) =>
    request<void>(`/workspaces/${enc(wsId)}/tags/${enc(tagId)}`, { method: 'DELETE' }),
  mergeTags: (wsId: string, body: TagMerge) =>
    request<Tag>(`/workspaces/${enc(wsId)}/tags/merge`, { method: 'POST', body }),
  tagItems: (wsId: string, tagId: string) =>
    request<TagItems>(`/workspaces/${enc(wsId)}/tags/${enc(tagId)}/items`),
  assignCollectionTag: (wsId: string, colId: string, tagId: string) =>
    request<void>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}/tags/${enc(tagId)}`, {
      method: 'PUT',
    }),
  unassignCollectionTag: (wsId: string, colId: string, tagId: string) =>
    request<void>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}/tags/${enc(tagId)}`, {
      method: 'DELETE',
    }),
  assignDocumentTag: (wsId: string, colId: string, docId: string, tagId: string) =>
    request<void>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents/${enc(docId)}/tags/${enc(tagId)}`,
      { method: 'PUT' },
    ),
  unassignDocumentTag: (wsId: string, colId: string, docId: string, tagId: string) =>
    request<void>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents/${enc(docId)}/tags/${enc(tagId)}`,
      { method: 'DELETE' },
    ),

  // ── BM25 indexing ─────────────────────────────────────────────────────────
  indexStatus: () => request<IndexStatusResponse>('/indexing/status'),
  indexDocument: (wsId: string, colId: string, docId: string) =>
    request<IndexEnqueueResponse>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents/${enc(docId)}/index`,
      { method: 'POST' },
    ),

  // ── Auth (console login sessions) ───────────────────────────────────────────
  login: (username: string, password: string) =>
    request<SessionResponse>('/auth/login', { method: 'POST', body: { username, password } }),
  me: () => request<User>('/auth/me'),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<SessionResponse>('/auth/change-password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    }),

  // ── Users, keys & permissions ───────────────────────────────────────────────
  listUsers: () => request<User[]>('/users'),
  createUser: (body: UserCreate) => request<CreatedUser>('/users', { method: 'POST', body }),
  getUser: (id: string) => request<User>(`/users/${enc(id)}`),
  updateUser: (id: string, body: UserUpdate) =>
    request<User>(`/users/${enc(id)}`, { method: 'PATCH', body }),
  deleteUser: (id: string) => request<void>(`/users/${enc(id)}`, { method: 'DELETE' }),
  resetUserPassword: (id: string) =>
    request<ResetPasswordResponse>(`/users/${enc(id)}/reset-password`, { method: 'POST' }),
  mintUserKey: (id: string, body: UserKeyCreate) =>
    request<MintedUserKey>(`/users/${enc(id)}/key`, { method: 'POST', body }),
  revokeUserKey: (id: string) => request<void>(`/users/${enc(id)}/key`, { method: 'DELETE' }),
  listPermissions: (id: string) => request<Permission[]>(`/users/${enc(id)}/permissions`),
  grantPermission: (id: string, body: GrantCreate) =>
    request<Permission>(`/users/${enc(id)}/permissions`, { method: 'POST', body }),
  revokePermission: (id: string, grantId: string) =>
    request<void>(`/users/${enc(id)}/permissions/${enc(grantId)}`, { method: 'DELETE' }),

  // ── Documents ─────────────────────────────────────────────────────────────
  listDocuments: (wsId: string, colId: string, query: DocumentQuery = {}) =>
    request<DocumentListResponse>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents${toQueryString(query)}`,
    ),
  uploadDocument: (
    wsId: string,
    colId: string,
    file: File,
    retentionDays: number | null = null,
  ) => {
    const form = new FormData()
    form.append('file', file)
    // Omit for a permanent document; 1-30 keeps it that many days (server validates).
    if (retentionDays != null) form.append('retention_days', String(retentionDays))
    return request<UploadAccepted>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents`,
      { method: 'POST', body: form },
    )
  },
  getDocumentStatus: (wsId: string, colId: string, docId: string) =>
    request<JobStatus>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents/${enc(docId)}/status`,
    ),
  deleteDocument: (wsId: string, colId: string, docId: string) =>
    request<void>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents/${enc(docId)}`,
      { method: 'DELETE' },
    ),
  /** Re-enqueue a failed (or stuck) document's ingestion — reuses the stored bytes. */
  reprocessDocument: (docId: string) =>
    request<{ job_id: string; document_id: string; status: string }>(
      `/documents/${enc(docId)}/reprocess`,
      { method: 'POST' },
    ),
  /**
   * Open a document's original file in a new tab. The fetch carries auth (which
   * a bare `window.open` cannot), so the bytes come back as a blob; the browser
   * renders viewable formats inline and hands everything else to the OS.
   */
  openDocument: async (docId: string) => {
    // Open the tab synchronously inside the click gesture, then point it at the
    // blob once fetched. Opening after the await would be blocked as a popup.
    const win = window.open('', '_blank')
    const { headers } = buildHeaders(undefined)
    try {
      const res = await fetch(`${BASE}/documents/${enc(docId)}/raw`, { headers })
      await raiseForStatus(res)
      const url = URL.createObjectURL(await res.blob())
      if (win) {
        win.opener = null // blob is same-origin; sever opener to restore noopener
        win.location.href = url
      } else {
        // Popups fully blocked — fall back to a download (less restricted).
        const a = document.createElement('a')
        a.href = url
        a.click()
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (e) {
      win?.close()
      throw e
    }
  },
  /**
   * Download a document's file under its real filename. Uses an anchor with
   * `download` (not a popup), so no synchronous-gesture trick is needed. Pass
   * `{ original: true }` to fetch the attached original source file instead of the parse.
   */
  downloadDocument: async (docId: string, filename: string, opts?: { original?: boolean }) => {
    const { headers } = buildHeaders(undefined)
    const path = `/documents/${enc(docId)}/raw${opts?.original ? '?original=1' : ''}`
    const res = await fetch(`${BASE}${path}`, { headers })
    await raiseForStatus(res)
    saveBlob(await res.blob(), filename)
  },

  // ── Ingestion queue (job history) ─────────────────────────────────────────
  listJobs: (query: JobQuery = {}) =>
    request<JobListResponse>(`/ingestion/jobs${toQueryString(query)}`),
  jobStats: () => request<JobStats>('/ingestion/jobs/stats'),
  /** Re-enqueue every currently-failed document matching `query`'s filters (status is forced to
   *  failed server-side). Returns how many documents were re-enqueued. */
  retryFailedJobs: (query: JobQuery = {}) =>
    request<{ retried: number }>(`/ingestion/jobs/retry-failed${toQueryString(query)}`, {
      method: 'POST',
    }),

  // ── Graph ─────────────────────────────────────────────────────────────────
  graph: (wsId: string, colId: string | null, linkTypes: string[] = ['tags']) => {
    const base = colId
      ? `/workspaces/${enc(wsId)}/collections/${enc(colId)}/graph`
      : `/workspaces/${enc(wsId)}/graph`
    const qs = linkTypes.map((t) => `link_types=${enc(t)}`).join('&')
    return request<GraphResponse>(`${base}?${qs}`)
  },

  // ── Search ────────────────────────────────────────────────────────────────
  search: (body: SearchRequest) => request<SearchResponse>('/search', { method: 'POST', body }),

  // ── Config ────────────────────────────────────────────────────────────────
  getConfig: () => request<AppConfig>('/config'),
  updateConfig: (body: AppConfig) => request<unknown>('/config', { method: 'PUT', body }),
  listOllamaModels: (baseUrl?: string) =>
    request<string[]>(`/config/ollama-models${baseUrl ? `?base_url=${enc(baseUrl)}` : ''}`),
  getAccelerator: () => request<Accelerator>('/config/accelerator'),

  // ── MCP ───────────────────────────────────────────────────────────────────
  // The tool catalogue is introspected live from the server, so the MCP settings page + SKILL.md
  // never drift from the registered tools.
  mcpTools: () => request<McpToolCatalog>('/mcp-tools'),

  // ── System ────────────────────────────────────────────────────────────────
  healthz: () => request<Health>('/healthz'),
}
