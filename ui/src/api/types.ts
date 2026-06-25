/**
 * TypeScript models mirroring the EmbedBase API responses.
 *
 * The backend returns two response styles (see [[D5 - React UI Plan]] risk #3):
 *   - Workspaces / collections / keys: raw SQLite row dicts (snake_case), where
 *     aggregate columns (`collection_count`, `document_count`, `chunk_count`) are
 *     only present on *some* endpoints. Those are typed optional so a consumer
 *     never trusts a count the endpoint did not actually return.
 *   - Search / documents: typed Pydantic models (mirrored 1:1 below).
 *
 * Field names intentionally match the API exactly — no camelCase remapping —
 * so the typed client stays a thin, verifiable pass-through.
 */

// ── Workspaces ──────────────────────────────────────────────────────────────

/** A workspace row. Table columns are always present; aggregates are not. */
export interface Workspace {
  id: string
  name: string
  description: string
  color: string
  icon: string
  created_at: string
  updated_at: string
  /** Present on `GET /workspaces` and `POST /workspaces`; absent on detail. */
  collection_count?: number
  /** Present only on `POST /workspaces` (always 0 at creation). */
  document_count?: number
  /** Present only on `POST /workspaces` (always 0 at creation). */
  chunk_count?: number
}

/** `GET /workspaces/{id}` — a workspace plus its (count-less) collection rows. */
export interface WorkspaceDetail extends Workspace {
  collections: Collection[]
}

// ── Collections ─────────────────────────────────────────────────────────────

/** A collection row. `document_count` is only present on the list endpoint. */
export interface Collection {
  id: string
  workspace_id: string
  name: string
  description: string
  color: string
  icon: string
  created_at: string
  updated_at: string
  /** Present on `GET .../collections`; absent on detail / nested-in-workspace. */
  document_count?: number
  /** Present only on `POST .../collections` (always 0 at creation). */
  chunk_count?: number
  /** Assigned tags, echoed by `GET .../collections`. */
  tags?: TagRef[]
}

// ── Tags ────────────────────────────────────────────────────────────────────

/** A tag as echoed inline on a tagged entity (collection / document row). */
export interface TagRef {
  id: string
  name: string
  color: string | null
}

/** A tag row. Usage counts are always returned by the tag endpoints. */
export interface Tag {
  id: string
  workspace_id: string
  name: string
  color: string | null
  created_at: string
  workspace_count: number
  collection_count: number
  document_count: number
}

export interface TagCreate {
  name: string
  color?: string | null
}

export type TagUpdate = Partial<{ name: string; color: string | null }>

export interface TagMerge {
  source_id: string
  target_id: string
}

/** `GET .../tags/{id}/items` — the entities correlated under a tag. */
export interface TagItems {
  collections: { id: string; name: string }[]
  documents: { id: string; filename: string; collection_id: string }[]
}

/** One AI-proposed tag with a confidence in `[0, 1]`. Ephemeral until applied. */
export interface TagSuggestion {
  name: string
  confidence: number
}

/** `POST .../suggest-tags` — ephemeral candidates for review (nothing persisted). */
export interface SuggestTagsResponse {
  suggestions: TagSuggestion[]
}

// ── Graph ─────────────────────────────────────────────────────────────────────

/** A graph node: a file (document) or a tag hub. Mirrors `api/schemas/graph.py`. */
export interface GraphNode {
  id: string
  label: string
  kind: 'file' | 'tag'
  heat: number
  heat_pct: number
  degree: number
  meta: Record<string, unknown>
}

/** A `file → tag` edge. */
export interface GraphEdge {
  source: string
  target: string
}

/** `GET .../graph` — nodes, edges, and a heat summary for a scope. */
export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  tag_counts: Record<string, number>
  max_heat: number
}

// ── API keys ────────────────────────────────────────────────────────────────

/** Key metadata as returned by `GET .../keys` — never includes the secret. */
export interface ApiKey {
  id: string
  collection_id: string
  key_prefix: string
  label: string
  created_at: string
  last_used_at: string | null
}

/**
 * `POST .../keys` — the only response that carries the raw secret (`raw_key`).
 * It is shown once and cannot be retrieved again; never persist it.
 */
export interface MintedApiKey {
  id: string
  collection_id: string
  key_prefix: string
  label: string
  created_at: string
  raw_key: string
}

// ── Documents ───────────────────────────────────────────────────────────────

/** Ingestion lifecycle states; `null` when a document has no job row yet. */
export type DocStatus = 'pending' | 'processing' | 'done' | 'failed' | 'deleting'

/** A row from `GET .../documents` (document joined to its latest job status). */
export interface DocumentSummary {
  document_id: string
  filename: string
  file_type: string
  file_size: number | null
  chunk_count: number | null
  created_at: string
  updated_at: string
  status: DocStatus | null
  /** Assigned tags, echoed by `GET .../documents`. */
  tags?: TagRef[]
}

/** `POST .../documents` (202) — the accepted-for-ingestion acknowledgement. */
export interface UploadAccepted {
  job_id: string
  document_id: string
  collection_id: string
  filename: string
  file_type: string
  file_size: number
  status: DocStatus
}

/** `GET .../documents/{id}/status` — the latest job record (or delete tombstone). */
export interface JobStatus {
  document_id: string
  status: DocStatus
  job_id?: string
  collection_id?: string
  filename?: string
  file_type?: string
  chunk_count?: number | null
  error?: string | null
  celery_task_id?: string | null
  processing_started_at?: string | null
  created_at?: string
  updated_at?: string
}

// ── Search (mirrors api/models/search.py) ───────────────────────────────────

export interface SearchFilters {
  language?: string | null
  filename?: string | null
  tags?: string[] | null
}

export type SearchModeRequest = 'hybrid' | 'semantic' | 'bm25'

export interface SearchRequest {
  query: string
  collection_ids: string[]
  top_k?: number
  mode?: SearchModeRequest
  hybrid_alpha?: number
  fan_out?: number | null
  filters?: SearchFilters | null
}

export interface SourceProvenance {
  collection_id: string
  collection_name: string
  workspace_id: string
  workspace_name: string
  document_id: string | null
  filename: string | null
  page_number: number | null
}

export interface SearchResult {
  chunk_id: string
  text: string
  score: number
  rank: number
  source: SourceProvenance | null
  metadata: Record<string, unknown>
}

export interface CollectionStat {
  name: string
  workspace_name: string
  retrieved_before_filter: number
  returned_after_filter: number
  contributed_to_top_k: number
}

export type SearchMode = 'hybrid' | 'semantic' | 'bm25' | 'semantic_only'

export interface SearchResponse {
  results: SearchResult[]
  collection_stats: Record<string, CollectionStat>
  query_embedding_ms: number
  search_ms: number
  total_ms: number
  search_mode: SearchMode
  under_delivered: boolean
}

// ── System ──────────────────────────────────────────────────────────────────

/** `GET /healthz` — the only unauthenticated endpoint. */
export interface Health {
  status: string
  service: string
  version: string
  vector_store: string
  vector_store_connected: boolean
  embedding_provider: string
  embedding_model: string
  embedding_model_loaded: boolean
  uptime_seconds: number
  /** The server's primary LAN IP, used to offer a reachable MCP address. */
  lan_ip: string
}

// ── Config (mirrors api/models/config.py; secrets masked by GET) ─────────────

/** The tag-suggester backend config (keyword = local, llm = Ollama/OpenAI-compat). */
export interface TagSuggesterConfig {
  backend: string // "keyword" | "llm"
  provider: string // "ollama" | "openai_compat"
  model: string
  base_url: string | null
  api_key: string | null // masked on GET as SECRET_MASK; write-only
  max_tags: number
  min_confidence: number
}

export interface TaggingConfig {
  suggester: TagSuggesterConfig
  auto_tag_on_ingest: boolean
}

/**
 * `GET /config` — the live AppConfig with secrets masked. Only the fields the
 * Settings UI edits are typed; every other section round-trips untouched on PUT.
 */
export interface AppConfig {
  tagging: TaggingConfig
  [section: string]: unknown
}

// ── Request bodies ──────────────────────────────────────────────────────────

export interface WorkspaceCreate {
  name: string
  description?: string
  color?: string
  icon?: string
}

export type WorkspaceUpdate = Partial<WorkspaceCreate>

export interface CollectionCreate {
  name: string
  description?: string
  color?: string
  icon?: string
}

export type CollectionUpdate = Partial<CollectionCreate>

export interface ApiKeyCreate {
  label?: string
}
