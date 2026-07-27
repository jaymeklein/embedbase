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
  /** Whether the caller may write this workspace (create collections in it).
   *  Present on `GET /workspaces` and `GET /workspaces/{id}`. */
  can_write?: boolean
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
  /** Whether the caller may write (edit/delete, ingest into) this collection.
   *  Present on `GET .../collections` and `GET .../collections/{id}`. */
  can_write?: boolean
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

// ── Users, keys & permissions ────────────────────────────────────────────────

/** Key metadata embedded on a user (never the secret); null when the user has no key. */
export interface UserKeySummary {
  id: string
  key_prefix: string
  label: string
  created_at: string
  last_used_at: string | null
}

/** A user row (`GET /users`, `GET /users/{id}`) with its API-key summary. */
export interface User {
  id: string
  username: string
  /** Optional — an account is identified by its username; null when it has no email. */
  email: string | null
  name: string
  is_active: boolean
  is_admin: boolean
  /** Per-user MCP rate limit (requests/min); 0 = inherit the global default. */
  rate_limit_rpm: number
  must_change_password: boolean
  created_at: string
  updated_at: string
  api_key: UserKeySummary | null
  /** Whether the user may create workspaces. Present only on `GET /auth/me`. */
  can_create_workspaces?: boolean
  /** Whether the user may manage tags (the `manage_tags` capability). Present only on `GET /auth/me`. */
  can_manage_tags?: boolean
}

/** `POST /users` — the created user plus the one-time login password (shown once). */
export interface CreatedUser extends User {
  temp_password: string
}

/** `POST /users/{id}/reset-password` — a fresh one-time login password (shown once). */
export interface ResetPasswordResponse {
  temp_password: string
}

/**
 * `POST /users/{id}/key` — the only response that carries the raw secret (`raw_key`).
 * Shown once and never retrievable again; never persist it. Minting replaces any
 * existing key (rotation).
 */
export interface MintedUserKey {
  id: string
  user_id: string
  key_prefix: string
  label: string
  created_at: string
  raw_key: string
}

/** `POST /auth/login` / `POST /auth/change-password` — the session token + change flag. */
export interface SessionResponse {
  access_token: string
  token_type: string
  must_change_password: boolean
}

export type PermissionLevel = 'read' | 'write'
/** `capability` grants a non-resource privilege (e.g. `resource_id: 'create_workspace'`);
 *  the others scope access to a workspace/collection/document. */
export type ResourceType = 'workspace' | 'collection' | 'document' | 'capability'

/** A grant row (`GET /users/{id}/permissions`) — one resource the user may access. */
export interface Permission {
  id: string
  user_id: string
  resource_type: ResourceType
  resource_id: string
  /** The resource's display name (workspace/collection name or document filename);
   *  null when the resource has since been deleted (a harmless dangling grant). */
  resource_name: string | null
  level: PermissionLevel
  created_at: string
}

// ── Documents ───────────────────────────────────────────────────────────────

/** Ingestion lifecycle states; `null` when a document has no job row yet.
 *  `uploading` is client-only — the multipart POST is still in flight. */
export type DocStatus = 'uploading' | 'pending' | 'processing' | 'done' | 'failed' | 'deleting'

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
  /** Whether the document's chunks are present in the BM25 corpus. */
  indexed?: boolean
  /** Assigned tags, echoed by `GET .../documents`. */
  tags?: TagRef[]
  /** Exact embedding model a finished document was ingested with, when known. */
  embedding_model?: string | null
  /** Storage backend holding the file (`local` / a configured S3 name). */
  storage_backend?: string | null
}

/** `GET .../documents` — one page of documents plus the full match count for the pager. */
export interface DocumentListResponse {
  items: DocumentSummary[]
  total: number
  limit: number
  offset: number
}

/** Query params for the documents listing. All optional and AND-combined server-side;
 *  `filename` is a case-insensitive substring and the `*_size`/`*_after`/`*_before` bounds
 *  are inclusive. */
export interface DocumentQuery {
  limit?: number
  offset?: number
  filename?: string
  file_type?: string
  status?: string
  indexed?: boolean
  embedding_model?: string
  storage_backend?: string
  min_size?: number
  max_size?: number
  created_after?: string
  created_before?: string
  updated_after?: string
  updated_before?: string
  tag?: string[]
}

// ── BM25 indexing ────────────────────────────────────────────────────────────

export interface CollectionIndexStatus {
  collection_id: string
  collection_name: string
  total: number
  indexed: number
  unindexed: number
  pending: number
  failed: number
}

export interface WorkspaceIndexStatus {
  workspace_id: string
  workspace_name: string
  collections: CollectionIndexStatus[]
}

export interface IndexStatusResponse {
  workspaces: WorkspaceIndexStatus[]
}

export interface IndexEnqueueResponse {
  task_id: string | null
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

// ── Ingestion queue (job history; mirrors GET /ingestion/jobs) ───────────────

/** A row from `GET /ingestion/jobs` — one `job_records` attempt joined to its collection name.
 *  `document_id` lets the queue page overlay live WebSocket progress onto the matching row. */
export interface JobSummary {
  job_id: string
  document_id: string
  collection_id: string
  collection_name: string | null // null when the collection was since deleted
  workspace_id: string | null // for the "open in collection" link; null if the collection is gone
  filename: string
  file_type: string
  status: string // pending | processing | done | failed | rate_limited
  chunk_count: number | null
  error: string | null
  created_at: string
  updated_at: string
}

/** `GET /ingestion/jobs` — one page of jobs plus the full match count for the pager. */
export interface JobListResponse {
  items: JobSummary[]
  total: number
  limit: number
  offset: number
}

/** Query params for the ingestion-jobs listing. All optional and AND-combined server-side;
 *  `filename`/`collection` are case-insensitive substrings and the `created_*` bounds inclusive. */
export interface JobQuery {
  limit?: number
  offset?: number
  status?: string
  filename?: string
  file_type?: string
  collection?: string
  /** Exact collection id — scopes an action to one collection, unlike the `collection` substring. */
  collection_id?: string
  created_after?: string
  created_before?: string
}

/** `GET /ingestion/jobs/stats` — live queue totals for the header. Server-side, so it drains as
 *  jobs finish (unlike the accumulating WebSocket buffer). */
export interface JobStats {
  counts: Record<string, number> // status → count; a status with no jobs is absent
  paused_seconds: number // >0 = ingestion paused on a provider quota backoff, resuming in N seconds
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
  page_range: string | null // "lo-hi" when an A2 span covers multiple pages, else null
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
  /** Reranker readiness: "ready" | "unavailable" (on-by-default but failed to load) | "disabled". */
  reranker: string
  uptime_seconds: number
  /** The server's primary LAN IP, used to offer a reachable MCP address. */
  lan_ip: string
}

// ── Config (mirrors api/models/config.py; secrets masked by GET) ─────────────

/** The embedding-model config. Changing provider/model changes vector dimensions. */
export interface EmbeddingConfig {
  provider: string // "ollama" | "sentence_transformers" | "openai_compat" | "gemini"
  model: string
  batch_size: number
  base_url: string | null
  api_key: string | null // masked on GET as SECRET_MASK; write-only
  concurrency: number
  output_dimensionality: number | null // gemini only
  max_rpm: number // max texts embedded/min against an external provider; 0 = unlimited
}

/**
 * The reranker (second-stage) config. Provider-pluggable, mirroring embeddings; optional and
 * degrades to RRF-only ranking when unavailable, so it never breaks search.
 */
export interface RerankerConfig {
  enabled: boolean
  provider: string // "cross_encoder" | "rerank_api" | "llm"
  model: string
  llm_provider: string // "ollama" | "openai_compat" | "gemini" (only when provider === "llm")
  base_url: string | null
  api_key: string | null // masked on GET as SECRET_MASK; write-only
  top_n: number
  timeout_seconds: number // round-tripped by the UI (edited in config.yaml)
}

/** Search / retrieval knobs. `expand_neighbors` drives A2 adjacency expansion (0 = off). */
export interface SearchConfig {
  default_top_k: number
  max_top_k: number
  retrieval_fan_out: number
  max_fan_out: number
  hybrid_default_alpha: number
  expand_neighbors: number
  max_expand_neighbors: number
  expand_char_budget: number
}

/** Parser config — only the PDF backend is edited in the UI; the rest round-trips. */
export interface ParserConfig {
  pdf_backend: string | null // null = never picked (UI pre-selects from GPU) | "pymupdf" | "docling"
  [key: string]: unknown
}

/** One configured storage backend. S3 credentials are masked on GET (write-only). */
export interface StorageBackend {
  type: 'local' | 's3'
  bucket?: string
  endpoint_url?: string | null
  public_endpoint_url?: string | null
  region?: string
  [key: string]: unknown
}

/**
 * Storage registry config. Backend *selection* stays in config.yaml/env; the UI only shows the
 * active backend read-only. Retention is now per-file (`retention_days` at upload), so the config
 * carries no live retention knob.
 */
export interface StorageConfig {
  default: string
  backends: Record<string, StorageBackend>
  /** @deprecated Superseded by per-file `retention_days`; no upload path reads this. Kept so an
   *  existing config.yaml that sets it still round-trips. */
  temp_retention_hours: number
}

/**
 * MCP transport config. `rate_limit_rpm` caps requests per minute per API key on the
 * mounted `/api/mcp` endpoint; the limiter reads it from live config per request, so a
 * save applies to the next MCP call. `enabled` needs a restart (it drives the mount) and
 * `max_results` is read at startup; both round-trip untouched from the UI.
 */
export interface MCPConfig {
  enabled: boolean
  rate_limit_rpm: number
  max_results: number
}

/** One MCP tool in the `GET /mcp-tools` catalogue — introspected from the live server. */
export interface McpToolInfo {
  name: string
  /** Python-like call signature synthesized from the tool's input schema, e.g. `(query, top_k=5)`. */
  signature: string
  /** One-line summary (first paragraph of the tool's description). */
  summary: string
}

/** A domain group of MCP tools (`GET /mcp-tools`). */
export interface McpToolGroup {
  group: string
  tools: McpToolInfo[]
}

/** `GET /mcp-tools` — the MCP tool surface, grouped by domain, that drives the MCP settings page. */
export interface McpToolCatalog {
  groups: McpToolGroup[]
}

/** `GET /config/accelerator` — GPU suitability for the docling PDF backend. */
export interface Accelerator {
  device: string // "cuda" | "cpu"
  capability: string | null // CUDA compute capability, e.g. "8.6"
  name: string | null // GPU name, when present
  compatible: boolean // Ampere+ CUDA → docling runs fast
}

/**
 * `GET /config` — the live AppConfig with secrets masked. Only the fields the
 * Settings UI edits are typed; every other section round-trips untouched on PUT.
 */
export interface AppConfig {
  embedding: EmbeddingConfig
  reranker: RerankerConfig
  search: SearchConfig
  parsers: ParserConfig
  storage: StorageConfig
  mcp: MCPConfig
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

export interface UserCreate {
  username: string
  /** Optional — omit or send blank for an account with no email. */
  email?: string
  name?: string
  is_active?: boolean
  is_admin?: boolean
  /** Per-user MCP rate limit (requests/min); 0 = inherit the global default. */
  rate_limit_rpm?: number
}

export type UserUpdate = Partial<{
  username: string
  email: string
  name: string
  is_active: boolean
  is_admin: boolean
  rate_limit_rpm: number
}>

export interface UserKeyCreate {
  label?: string
}

export interface GrantCreate {
  resource_type: ResourceType
  resource_id: string
  level: PermissionLevel
}
