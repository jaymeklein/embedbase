/**
 * Typed, authenticated API client for the EmbedBase backend.
 *
 * Every request injects the master key as `Authorization: Bearer <key>`. A 401
 * triggers `notifyUnauthorized()` so the app can lock and return to the unlock
 * screen, then throws an {@link ApiError} carrying the status code.
 */

import { getMasterKey, notifyUnauthorized } from './tokenStore'
import type {
  ApiKey,
  AppConfig,
  Collection,
  CollectionCreate,
  CollectionUpdate,
  DocumentSummary,
  Health,
  JobStatus,
  MintedApiKey,
  ApiKeyCreate,
  GraphResponse,
  SearchRequest,
  SearchResponse,
  SuggestTagsResponse,
  Tag,
  TagCreate,
  TagItems,
  TagMerge,
  TagUpdate,
  UploadAccepted,
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
  const key = getMasterKey()
  if (key) headers.set('Authorization', `Bearer ${key}`)

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

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options
  const { headers, payload } = buildHeaders(body)

  const res = await fetch(`${BASE}${path}`, { method, headers, body: payload, signal })

  if (res.status === 401) {
    notifyUnauthorized()
    throw new ApiError(401, 'Master key rejected. Please unlock again.')
  }
  if (!res.ok) {
    throw new ApiError(res.status, await errorMessage(res))
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const enc = encodeURIComponent

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
  suggestCollectionTags: (wsId: string, colId: string) =>
    request<SuggestTagsResponse>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/suggest-tags`,
      { method: 'POST' },
    ),
  suggestDocumentTags: (wsId: string, colId: string, docId: string) =>
    request<SuggestTagsResponse>(
      `/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents/${enc(docId)}/suggest-tags`,
      { method: 'POST' },
    ),

  // ── API keys ──────────────────────────────────────────────────────────────
  listApiKeys: (wsId: string, colId: string) =>
    request<ApiKey[]>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}/keys`),
  mintApiKey: (wsId: string, colId: string, body: ApiKeyCreate) =>
    request<MintedApiKey>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}/keys`, {
      method: 'POST',
      body,
    }),
  revokeApiKey: (wsId: string, colId: string, keyId: string) =>
    request<void>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}/keys/${enc(keyId)}`, {
      method: 'DELETE',
    }),

  // ── Documents ─────────────────────────────────────────────────────────────
  listDocuments: (wsId: string, colId: string) =>
    request<DocumentSummary[]>(`/workspaces/${enc(wsId)}/collections/${enc(colId)}/documents`),
  uploadDocument: (wsId: string, colId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
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
      if (res.status === 401) {
        notifyUnauthorized()
        throw new ApiError(401, 'Master key rejected. Please unlock again.')
      }
      if (!res.ok) throw new ApiError(res.status, await errorMessage(res))
      const url = URL.createObjectURL(await res.blob())
      if (win) win.location.href = url
      else {
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

  // ── System ────────────────────────────────────────────────────────────────
  healthz: () => request<Health>('/healthz'),
}
