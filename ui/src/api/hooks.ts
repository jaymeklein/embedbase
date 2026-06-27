/**
 * TanStack Query bindings over the typed {@link api} client.
 *
 * The global 401 → lock behaviour lives in the client itself (it calls
 * `notifyUnauthorized()` before throwing), so these hooks stay declarative.
 * `retry: false` keeps a revoked-key 401 from being retried before the lock
 * handler fires.
 */

import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type {
  ApiKeyCreate,
  AppConfig,
  CollectionCreate,
  CollectionUpdate,
  DocumentSummary,
  SearchRequest,
  SearchResponse,
  Tag,
  TagCreate,
  TagMerge,
  TagUpdate,
  WorkspaceCreate,
  WorkspaceUpdate,
} from './types'

/** Central query-key factory — the single source of truth for cache keys. */
export const qk = {
  health: ['health'] as const,
  workspaces: ['workspaces'] as const,
  workspace: (id: string) => ['workspace', id] as const,
  collections: (wsId: string) => ['workspaces', wsId, 'collections'] as const,
  collection: (wsId: string, colId: string) =>
    ['workspaces', wsId, 'collections', colId] as const,
  documents: (wsId: string, colId: string) =>
    ['workspaces', wsId, 'collections', colId, 'documents'] as const,
  apiKeys: (wsId: string, colId: string) =>
    ['workspaces', wsId, 'collections', colId, 'keys'] as const,
  config: ['config'] as const,
  ollamaModels: (baseUrl: string) => ['config', 'ollama-models', baseUrl] as const,
  tags: (wsId: string) => ['workspaces', wsId, 'tags'] as const,
  tagItems: (wsId: string, tagId: string) =>
    ['workspaces', wsId, 'tags', tagId, 'items'] as const,
  graph: (wsId: string, colId: string | null, linkTypes: string[]) =>
    ['workspaces', wsId, 'graph', colId, linkTypes] as const,
  indexStatus: ['indexing', 'status'] as const,
}

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: () => api.healthz(), retry: false })
}

/** Sentinel the API returns for a set secret; echo it back unchanged to keep it. */
export const SECRET_MASK = '__SECRET_SET__'

/** Live runtime config (secrets masked). */
export function useConfig() {
  return useQuery({ queryKey: qk.config, queryFn: () => api.getConfig(), retry: false })
}

/**
 * Models installed on the Ollama server, for the suggester model picker.
 * Re-fetches when the edited base URL changes; only enabled when asked for.
 */
export function useOllamaModels(baseUrl: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.ollamaModels(baseUrl),
    queryFn: () => api.listOllamaModels(baseUrl || undefined),
    enabled,
    retry: false,
  })
}

/**
 * Test Ollama connectivity on demand: resolves with the installed models when
 * the server is reachable, rejects with the API error when it is not.
 */
export function useTestOllama() {
  return useMutation({ mutationFn: (baseUrl: string) => api.listOllamaModels(baseUrl || undefined) })
}

/** Apply a full config payload (PUT /config) and refetch the live config. */
export function useUpdateConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: AppConfig) => api.updateConfig(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.config }),
  })
}

/**
 * Whether auto-tagging will actually produce tags on ingest right now.
 *
 * Needs `auto_tag_on_ingest` on AND the suggester provider reachable. Returns
 * `undefined` while config/probe is still resolving so callers can avoid a false
 * "no provider" warning before the answer is known.
 *
 * ponytail: only Ollama is probed for reachability (the one probe that exists);
 * other providers are treated as on when configured — add a probe if one exists.
 */
export function useAutoTagAvailability(): { available: boolean | undefined } {
  const config = useConfig()
  const tagging = config.data?.tagging
  const autoTag = tagging?.auto_tag_on_ingest === true
  const provider = tagging?.suggester.provider
  const baseUrl = tagging?.suggester.base_url ?? ''
  const ollama = useOllamaModels(baseUrl, Boolean(autoTag && provider === 'ollama'))

  if (config.isLoading) return { available: undefined }
  if (!autoTag) return { available: false }
  if (provider === 'ollama') {
    if (ollama.isLoading) return { available: undefined }
    return { available: ollama.isSuccess }
  }
  return { available: true }
}

export function useWorkspaces() {
  return useQuery({ queryKey: qk.workspaces, queryFn: () => api.listWorkspaces(), retry: false })
}

export function useWorkspace(id: string) {
  return useQuery({
    queryKey: qk.workspace(id),
    queryFn: () => api.getWorkspace(id),
    enabled: Boolean(id),
    retry: false,
  })
}

export function useCollections(wsId: string) {
  return useQuery({
    queryKey: qk.collections(wsId),
    queryFn: () => api.listCollections(wsId),
    enabled: Boolean(wsId),
    retry: false,
  })
}

export function useDocuments(wsId: string, colId: string) {
  return useQuery({
    queryKey: qk.documents(wsId, colId),
    queryFn: () => api.listDocuments(wsId, colId),
    enabled: Boolean(wsId) && Boolean(colId),
    retry: false,
    // No polling: live ingestion status streams over WebSocket
    // (useIngestionProgress), which invalidates this query when a doc settles.
  })
}

/** Search is a POST whose result is not cached by key — expose it as a mutation. */
export function useSearch() {
  return useMutation<SearchResponse, Error, SearchRequest>({
    mutationFn: (body) => api.search(body),
  })
}

/** A document row tagged with the workspace/collection it was fanned out from. */
export interface RecentDocument extends DocumentSummary {
  workspace_id: string
  collection_id: string
}

/**
 * Best-effort cross-collection recent-activity feed for the dashboard.
 *
 * Fans out workspaces → collections → documents via `useQueries`, flattens the
 * leaves, and returns the most-recently-updated rows. Partial data renders as
 * it resolves and a single failed leaf (`retry: false`) never blocks the rest.
 */
export function useRecentDocuments(limit = 8): { documents: RecentDocument[]; isLoading: boolean } {
  const workspaces = useWorkspaces()
  const wsList = workspaces.data ?? []

  const collectionQueries = useQueries({
    queries: wsList.map((ws) => ({
      queryKey: qk.collections(ws.id),
      queryFn: () => api.listCollections(ws.id),
      enabled: Boolean(ws.id),
      retry: false,
    })),
  })

  const pairs = wsList.flatMap((ws, i) =>
    (collectionQueries[i]?.data ?? []).map((col) => ({ wsId: ws.id, colId: col.id })),
  )

  const documentQueries = useQueries({
    queries: pairs.map(({ wsId, colId }) => ({
      queryKey: qk.documents(wsId, colId),
      queryFn: () => api.listDocuments(wsId, colId),
      enabled: Boolean(wsId) && Boolean(colId),
      retry: false,
    })),
  })

  const documents = pairs
    .flatMap(({ wsId, colId }, i) =>
      (documentQueries[i]?.data ?? []).map((d) => ({
        ...d,
        workspace_id: wsId,
        collection_id: colId,
      })),
    )
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, limit)

  const isLoading =
    workspaces.isLoading ||
    collectionQueries.some((q) => q.isLoading) ||
    documentQueries.some((q) => q.isLoading)

  return { documents, isLoading }
}

/** Invalidate the workspace list so list/aggregate views refetch after a write. */
function useInvalidateWorkspaces(): () => Promise<void> {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: qk.workspaces })
}

export function useCreateWorkspace() {
  const invalidate = useInvalidateWorkspaces()
  return useMutation({
    mutationFn: (body: WorkspaceCreate) => api.createWorkspace(body),
    onSuccess: invalidate,
  })
}

export function useUpdateWorkspace() {
  const invalidate = useInvalidateWorkspaces()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: WorkspaceUpdate }) =>
      api.updateWorkspace(id, body),
    onSuccess: invalidate,
  })
}

export function useDeleteWorkspace() {
  const invalidate = useInvalidateWorkspaces()
  return useMutation({
    mutationFn: (id: string) => api.deleteWorkspace(id),
    onSuccess: invalidate,
  })
}

/**
 * Refresh a workspace's collection list plus the workspace aggregates, since a
 * collection create/delete changes the parent's `collection_count`.
 */
function useInvalidateCollections(wsId: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.collections(wsId) }),
      queryClient.invalidateQueries({ queryKey: qk.workspaces }),
      queryClient.invalidateQueries({ queryKey: qk.workspace(wsId) }),
    ])
  }
}

export function useCreateCollection(wsId: string) {
  const invalidate = useInvalidateCollections(wsId)
  return useMutation({
    mutationFn: (body: CollectionCreate) => api.createCollection(wsId, body),
    onSuccess: invalidate,
  })
}

export function useUpdateCollection(wsId: string) {
  const invalidate = useInvalidateCollections(wsId)
  return useMutation({
    mutationFn: ({ colId, body }: { colId: string; body: CollectionUpdate }) =>
      api.updateCollection(wsId, colId, body),
    onSuccess: invalidate,
  })
}

export function useDeleteCollection(wsId: string) {
  const invalidate = useInvalidateCollections(wsId)
  return useMutation({
    mutationFn: (colId: string) => api.deleteCollection(wsId, colId),
    onSuccess: invalidate,
  })
}

export function useApiKeys(wsId: string, colId: string) {
  return useQuery({
    queryKey: qk.apiKeys(wsId, colId),
    queryFn: () => api.listApiKeys(wsId, colId),
    enabled: Boolean(wsId) && Boolean(colId),
    retry: false,
  })
}

/** Invalidate the key list for one collection after a mint/revoke. */
function useInvalidateApiKeys(wsId: string, colId: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: qk.apiKeys(wsId, colId) })
}

export function useMintApiKey(wsId: string, colId: string) {
  const invalidate = useInvalidateApiKeys(wsId, colId)
  return useMutation({
    mutationFn: (body: ApiKeyCreate) => api.mintApiKey(wsId, colId, body),
    onSuccess: invalidate,
  })
}

export function useRevokeApiKey(wsId: string, colId: string) {
  const invalidate = useInvalidateApiKeys(wsId, colId)
  return useMutation({
    mutationFn: (keyId: string) => api.revokeApiKey(wsId, colId, keyId),
    onSuccess: invalidate,
  })
}

export function useCollection(wsId: string, colId: string) {
  return useQuery({
    queryKey: qk.collection(wsId, colId),
    queryFn: () => api.getCollection(wsId, colId),
    enabled: Boolean(wsId) && Boolean(colId),
    retry: false,
  })
}

/**
 * Refresh a collection's document list plus its document-count aggregate after
 * an upload or delete.
 */
function useInvalidateDocuments(wsId: string, colId: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.documents(wsId, colId) }),
      queryClient.invalidateQueries({ queryKey: qk.collections(wsId) }),
    ])
  }
}

export function useUploadDocument(wsId: string, colId: string) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateDocuments(wsId, colId)
  return useMutation({
    mutationFn: (file: File) => api.uploadDocument(wsId, colId, file),
    // Show an optimistic "uploading" row while the multipart POST is in flight —
    // before the 202 returns a real document_id. onSettled's refetch then swaps it
    // for the server's pending row (or onError removes it).
    onMutate: async (file: File) => {
      const key = qk.documents(wsId, colId)
      await queryClient.cancelQueries({ queryKey: key })
      const tempId = `upload-${file.name}-${Date.now()}`
      const placeholder: DocumentSummary = {
        document_id: tempId,
        filename: file.name,
        file_type: file.name.split('.').pop()?.toLowerCase() ?? '',
        file_size: file.size,
        chunk_count: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        status: 'uploading',
      }
      queryClient.setQueryData<DocumentSummary[]>(key, (prev) =>
        prev ? [placeholder, ...prev] : [placeholder],
      )
      return { tempId }
    },
    onError: (_err, _file, ctx) => {
      const key = qk.documents(wsId, colId)
      queryClient.setQueryData<DocumentSummary[]>(key, (prev) =>
        prev?.filter((d) => d.document_id !== ctx?.tempId),
      )
    },
    onSettled: invalidate,
  })
}

export function useDeleteDocument(wsId: string, colId: string) {
  const invalidate = useInvalidateDocuments(wsId, colId)
  return useMutation({
    mutationFn: (docId: string) => api.deleteDocument(wsId, colId, docId),
    onSuccess: invalidate,
  })
}

// ── BM25 indexing ───────────────────────────────────────────────────────────

/** BM25 index coverage grouped by workspace → collection. */
export function useIndexStatus() {
  return useQuery({
    queryKey: qk.indexStatus,
    queryFn: () => api.indexStatus(),
    retry: false,
    // Poll while any document is mid-ingestion so freshly indexed docs surface.
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return false
      const busy = data.workspaces.some((ws) =>
        ws.collections.some((c) => c.pending > 0),
      )
      return busy ? 3000 : false
    },
  })
}

/** Invalidate the index overview plus a collection's document list after a (re)index. */
function useInvalidateIndex(wsId?: string, colId?: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.indexStatus }),
      ...(wsId && colId
        ? [queryClient.invalidateQueries({ queryKey: qk.documents(wsId, colId) })]
        : []),
    ])
  }
}

/** Enqueue a BM25 (re)index of a single document. */
export function useIndexDocument(wsId: string, colId: string) {
  const invalidate = useInvalidateIndex(wsId, colId)
  return useMutation({
    mutationFn: (docId: string) => api.indexDocument(wsId, colId, docId),
    onSuccess: invalidate,
  })
}

/** Enqueue a BM25 (re)index of an entire collection. */
export function useIndexCollection() {
  const invalidate = useInvalidateIndex()
  return useMutation({
    mutationFn: ({ wsId, colId }: { wsId: string; colId: string }) =>
      api.indexCollection(wsId, colId),
    onSuccess: invalidate,
  })
}

// ── Tags ──────────────────────────────────────────────────────────────────

export function useTags(wsId: string) {
  return useQuery({
    queryKey: qk.tags(wsId),
    queryFn: () => api.listTags(wsId),
    enabled: Boolean(wsId),
    retry: false,
  })
}

export function useTagItems(wsId: string, tagId: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.tagItems(wsId, tagId),
    queryFn: () => api.tagItems(wsId, tagId),
    enabled: enabled && Boolean(wsId) && Boolean(tagId),
    retry: false,
  })
}

/** Refresh the workspace tag list after any tag-definition write. */
function useInvalidateTags(wsId: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: qk.tags(wsId) })
}

export function useCreateTag(wsId: string) {
  const invalidate = useInvalidateTags(wsId)
  return useMutation({
    mutationFn: (body: TagCreate) => api.createTag(wsId, body),
    onSuccess: invalidate,
  })
}

export function useUpdateTag(wsId: string) {
  const invalidate = useInvalidateTags(wsId)
  return useMutation({
    mutationFn: ({ tagId, body }: { tagId: string; body: TagUpdate }) =>
      api.updateTag(wsId, tagId, body),
    onSuccess: invalidate,
  })
}

export function useDeleteTag(wsId: string) {
  const invalidate = useInvalidateTags(wsId)
  return useMutation({
    mutationFn: (tagId: string) => api.deleteTag(wsId, tagId),
    onSuccess: invalidate,
  })
}

export function useMergeTag(wsId: string) {
  const invalidate = useInvalidateTags(wsId)
  return useMutation({
    mutationFn: (body: TagMerge) => api.mergeTags(wsId, body),
    onSuccess: invalidate,
  })
}

/** Refresh the collection list (echoed tags) + the tag list (usage counts). */
function useInvalidateCollectionTags(wsId: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.collections(wsId) }),
      queryClient.invalidateQueries({ queryKey: qk.tags(wsId) }),
    ])
  }
}

export function useAssignCollectionTag(wsId: string) {
  const invalidate = useInvalidateCollectionTags(wsId)
  return useMutation({
    mutationFn: ({ colId, tagId }: { colId: string; tagId: string }) =>
      api.assignCollectionTag(wsId, colId, tagId),
    onSuccess: invalidate,
  })
}

export function useUnassignCollectionTag(wsId: string) {
  const invalidate = useInvalidateCollectionTags(wsId)
  return useMutation({
    mutationFn: ({ colId, tagId }: { colId: string; tagId: string }) =>
      api.unassignCollectionTag(wsId, colId, tagId),
    onSuccess: invalidate,
  })
}

/** Refresh the document list (echoed tags) + the tag list (usage counts). */
function useInvalidateDocumentTags(wsId: string, colId: string): () => Promise<void> {
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.documents(wsId, colId) }),
      queryClient.invalidateQueries({ queryKey: qk.tags(wsId) }),
    ])
  }
}

export function useAssignDocumentTag(wsId: string, colId: string) {
  const invalidate = useInvalidateDocumentTags(wsId, colId)
  return useMutation({
    mutationFn: ({ docId, tagId }: { docId: string; tagId: string }) =>
      api.assignDocumentTag(wsId, colId, docId, tagId),
    onSuccess: invalidate,
  })
}

export function useUnassignDocumentTag(wsId: string, colId: string) {
  const invalidate = useInvalidateDocumentTags(wsId, colId)
  return useMutation({
    mutationFn: ({ docId, tagId }: { docId: string; tagId: string }) =>
      api.unassignDocumentTag(wsId, colId, docId, tagId),
    onSuccess: invalidate,
  })
}

/** Ephemeral tag suggestions for a collection (nothing persists until applied). */
export function useSuggestCollectionTags(wsId: string, colId: string) {
  return useMutation({ mutationFn: () => api.suggestCollectionTags(wsId, colId) })
}

/** Ephemeral tag suggestions for a document (nothing persists until applied). */
export function useSuggestDocumentTags(wsId: string, colId: string, docId: string) {
  return useMutation({ mutationFn: () => api.suggestDocumentTags(wsId, colId, docId) })
}

/** Normalize a name the same way the backend does, for exact-match detection. */
function normalizeName(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, ' ')
}

/**
 * Returns `apply(names, assign)`: resolve each tag name to an id (creating any
 * that don't exist yet in the workspace), then assign it via the caller's
 * `assign` callback. Used to apply approved AI suggestions through the regular
 * create + assign endpoints, so nothing persists until the user approves.
 *
 * ponytail: reads the cached tag list to map names→ids; a tag created elsewhere
 * since the last fetch falls through to create and may 409 — acceptable, the
 * error surfaces to the caller.
 */
export function useApplyTagsByName(wsId: string) {
  const queryClient = useQueryClient()
  const createTag = useCreateTag(wsId)
  return async (names: string[], assign: (tagId: string) => Promise<unknown>) => {
    const existing = queryClient.getQueryData<Tag[]>(qk.tags(wsId)) ?? []
    const byName = new Map(existing.map((t) => [t.name, t.id]))
    for (const name of names) {
      const id = byName.get(normalizeName(name)) ?? (await createTag.mutateAsync({ name })).id
      await assign(id)
    }
  }
}

/** Fetch the tag-correlation graph for a workspace (or one collection within it). */
export function useGraph(wsId: string, colId: string | null, linkTypes: string[] = ['tags']) {
  return useQuery({
    queryKey: qk.graph(wsId, colId, linkTypes),
    queryFn: () => api.graph(wsId, colId, linkTypes),
    enabled: Boolean(wsId),
    retry: false,
  })
}

/** Fetch a single document's latest job record on demand (e.g. a failure reason). */
export function useDocumentStatus(wsId: string, colId: string, docId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...qk.documents(wsId, colId), docId, 'status'] as const,
    queryFn: () => api.getDocumentStatus(wsId, colId, docId),
    enabled: enabled && Boolean(docId),
    retry: false,
  })
}
