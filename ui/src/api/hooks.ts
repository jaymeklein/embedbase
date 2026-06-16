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
  CollectionCreate,
  CollectionUpdate,
  DocumentSummary,
  SearchRequest,
  SearchResponse,
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
  tags: (wsId: string) => ['workspaces', wsId, 'tags'] as const,
  tagItems: (wsId: string, tagId: string) =>
    ['workspaces', wsId, 'tags', tagId, 'items'] as const,
}

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: () => api.healthz(), retry: false })
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

/** Ingestion states that are still in flight and warrant active polling. */
const ACTIVE_STATUSES = ['pending', 'processing', 'deleting']

export function useDocuments(wsId: string, colId: string) {
  return useQuery({
    queryKey: qk.documents(wsId, colId),
    queryFn: () => api.listDocuments(wsId, colId),
    enabled: Boolean(wsId) && Boolean(colId),
    retry: false,
    // Poll only while a document is mid-ingestion; stop once all settle.
    refetchInterval: (query) => {
      const docs = query.state.data
      if (!docs) return false
      return docs.some((d) => d.status && ACTIVE_STATUSES.includes(d.status)) ? 2000 : false
    },
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
  const invalidate = useInvalidateDocuments(wsId, colId)
  return useMutation({
    mutationFn: (file: File) => api.uploadDocument(wsId, colId, file),
    onSuccess: invalidate,
  })
}

export function useDeleteDocument(wsId: string, colId: string) {
  const invalidate = useInvalidateDocuments(wsId, colId)
  return useMutation({
    mutationFn: (docId: string) => api.deleteDocument(wsId, colId, docId),
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

/** Fetch a single document's latest job record on demand (e.g. a failure reason). */
export function useDocumentStatus(wsId: string, colId: string, docId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...qk.documents(wsId, colId), docId, 'status'] as const,
    queryFn: () => api.getDocumentStatus(wsId, colId, docId),
    enabled: enabled && Boolean(docId),
    retry: false,
  })
}
