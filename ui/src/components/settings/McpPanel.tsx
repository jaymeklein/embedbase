import { useEffect, useState } from 'react'
import { Download, ExternalLink } from 'lucide-react'
import { api } from '../../api/client'
import { useMcpTools } from '../../api/hooks'
import type { McpToolGroup } from '../../api/types'
import { Button, Card, CopyButton, Field, Input, QueryError, Skeleton } from '../ui'
import { buildZip, type ZipEntry } from '../../lib/zip'
import { saveBlob } from '../../lib/download'

/** All tag tools need the manage_tags capability; assign/unassign also need write on the target. */
const TAGS_NOTE = 'Tag tools need the manage_tags permission; assigning to a collection/document also needs write on it.'

/** Generic MCP-client config snippet pointing at this deployment's streamable-HTTP endpoint. */
function clientConfig(origin: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        embedbase: {
          type: 'http',
          url: `${origin}/api/mcp/`,
          headers: { Authorization: 'Bearer <YOUR_API_KEY>' },
        },
      },
    },
    null,
    2,
  )
}

/** The skill bundle handed to an agent so it knows how to drive this MCP server: a lean SKILL.md plus
 *  on-demand reference files, so a read-only session never loads the upload rules. The tool list is the
 *  live catalogue from the server, so it tracks the real surface with no hand-kept copy. */
function skillBundle(origin: string, groups: McpToolGroup[]): ZipEntry[] {
  const tools = groups
    .map(
      (g) =>
        `### ${g.group}\n${g.tools.map((t) => `- ${t.name}${t.signature} — ${t.summary}`).join('\n')}`,
    )
    .join('\n\n')

  const skill = `---
name: embedbase
description: Drive EmbedBase entirely from chat through its MCP server — search, upload/download, verify file integrity, re-ingest, and check ingestion status of documents, and manage workspaces, collections, and tags. Use when the user asks to query their knowledge base, add/remove/re-ingest documents, verify a stored file is intact or up to date, or organize workspaces, collections, and tags.
---

# EmbedBase MCP

EmbedBase is a local-first document embedding and retrieval system. This skill connects to its MCP
server to run it end to end from chat — search, upload/download, ingestion status, and
workspace/collection/tag management — without opening the console.

Read a reference file below only when the task needs it, so a read-only session never pulls in the
upload rules:
- references/searching.md — search options (top_k, hybrid, filters, expanding a chunk).
- references/uploading.md — accepted formats, converting a PDF to full text, the upload steps, and
  keeping the original source file.
- references/tools.md — the full tool catalogue.

## Connection

- Transport: HTTP (streamable)
- URL: ${origin}/api/mcp/
- Auth: send your EmbedBase API key on every request, either
  - Authorization: Bearer <API_KEY>, or
  - X-API-Key: <API_KEY>

Use the master key, or a per-user key for scoped access. Every tool acts as that key's user and
respects its read/write grants — you only see and change what it is allowed to. A missing or wrong
key returns 401; the server is rate-limited per key (429 when the budget is exceeded). Admin
concerns (app config, user/key management) stay in the console and are not exposed as tools.

## Querying (the common case)

Most sessions only read — no upload involved. To answer from the knowledge base:

1. list_workspaces() to resolve workspace + collection ids — never invent them.
2. search_documents(query, [collection_id]) — the primary retrieval tool; it returns the most
   relevant chunks, not whole documents. For top_k / hybrid / filters and when to expand a chunk,
   read references/searching.md.
3. Answer, citing results by filename.

## Adding a document (only when the user asks to)

Skip this for read-only requests. When the user wants to ingest a file, read references/uploading.md —
it covers the accepted formats (.pdf, .md/.markdown, .txt), converting a PDF to full text first, the
two-step upload, and optionally keeping the original source file.

## Verifying a stored file

When a user worries their upload was altered, or wants to know whether the stored copy is still
current, call get_checksum(document_id) — it recomputes a SHA-256 over the stored bytes on every call
(nothing cached) and returns the digest plus ingested_at. Compare the digest to the user's local
sha256sum <file>: equal means the stored file is byte-identical to what they sent, and ingested_at
tells them whether the stored copy predates a newer local file. Add original=true to verify an
attached original source file instead of the parse.

## REST API reference

A standalone OpenAPI reference of just the integration endpoints (search +
workspace/collection/document access) — not the app's internal endpoints:
- Swagger UI: ${origin}/api/reference
- Raw spec:   ${origin}/api/reference.json

Read the spec to learn the exact request/response shapes behind the tools.
`

  const searching = `# Searching

search_documents is the primary retrieval tool — reach for it first for any "what does the knowledge
base say about X" question. It runs hybrid semantic + keyword (BM25) ranking and returns only the most
relevant chunks, bounded by top_k, so you pull just what answers the question instead of whole documents.

- Start here: search_documents(query, [collection_id], top_k). Raise top_k (or re-run) when the response
  sets more_available and the answer still looks incomplete; narrow with filters ({ filename, tags }), or
  set hybrid=false for semantic-only.
- get_document_chunks is a follow-up, not a reader — pass the chunk_ids a search returned to expand their
  surrounding context. Do not use it to pull a whole document into context to "read" it: on a large
  document that drags in every chunk, relevant or not — exactly what search exists to avoid. (Paging a
  full document via limit/offset is only for a deliberately small, known document.)
`

  const uploading = `# Adding a document — formats & uploading

Read this only when the user wants to ingest a file; ignore it for read-only requests.

## Accepted formats

Only three upload formats are accepted — reject anything else before uploading:

- .pdf
- .md / .markdown
- .txt

When you produce the content yourself, prefer Markdown — structure survives as text, so it embeds and
retrieves best.

## Converting a PDF before upload

A raw PDF buries structure and non-text data that plain extraction drops. When a PDF holds tables or
images, do not upload lossily-extracted text — convert the whole document to full text and upload it
as .md (or .txt) so nothing searchable is lost:

- Text — transcribe the complete document text in reading order; never summarise or truncate.
- Tables — reproduce each one as a real Markdown table (a header row, a divider row, and one row per
  record). Never flatten a table into prose.
- Images, figures, charts, and diagrams — replace each with both a short description (what it shows)
  and a text transcription (every label, caption, axis value, and any text visible in the image).

The uploaded file should read as a faithful, fully-textual rendering of the source — so search can
reach the tables and image content a raw PDF would otherwise hide.

## Uploading (presigned, two steps)

1. request_upload(collection_id, filename) → PUT the bytes to the returned upload_url →
   confirm_upload(document_id).
2. Poll get_document_status(document_id) until "done".

## Keeping the original

The converted .md is what gets embedded and searched — the source PDF is not needed for search. When
the user still wants the original kept (to re-read or hand off later), attach it to the *same*
document after confirming the upload, so the two stay correlated:

1. request_original_upload(document_id, filename) → returns a presigned upload_url.
2. PUT the original bytes to that upload_url.
3. confirm_original_upload(document_id).

The original is stored alongside the parse, is never embedded (so it adds no duplicate search hits),
and is downloadable any time via download_document(document_id, original=true). Only attach an
original when the user asks to keep it — most uploads don't need one.
`

  const toolsDoc = `# Tool catalogue

Every tool acts as your key's user and respects its read/write grants — you only see and change what it
is allowed to. Your MCP client also receives these tools directly from the server; this file is a
human-readable index.

${tools}

${TAGS_NOTE}
`

  return [
    { name: 'SKILL.md', content: skill },
    { name: 'references/searching.md', content: searching },
    { name: 'references/uploading.md', content: uploading },
    { name: 'references/tools.md', content: toolsDoc },
  ]
}

/** A labelled monospace value row with a copy button. */
function ValueRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-ink-faint">{label}</span>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded-control bg-canvas px-2 py-1.5 font-mono text-[13px] text-ink">
          {value}
        </code>
        <CopyButton text={value} label="Copy" />
      </div>
    </div>
  )
}

/**
 * MCP server tab: connection details, a client-config snippet, the downloadable SKILL.md, the tool
 * catalogue, and links to the live Swagger / OpenAPI reference. The endpoint is derived from the
 * current origin so it's correct wherever the console is served; the API key is only a placeholder,
 * never embedded. The tool list + SKILL.md are built from the live `GET /mcp-tools` catalogue, so
 * they track the registered tools automatically.
 */
export function McpPanel() {
  // Address an MCP client uses to reach this server. Defaults to how the console
  // was reached; when that's localhost it only works on this machine, so we swap
  // in the LAN IP the server reports (same protocol/port) — reachable by other
  // devices without the user hunting for it. Drives every URL below.
  const [address, setAddress] = useState(window.location.origin)
  const isLocal = /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(address)

  useEffect(() => {
    const loc = window.location
    if (!/^(localhost|127\.0\.0\.1)$/.test(loc.hostname)) return
    api
      .healthz()
      .then(({ lan_ip }) => {
        if (lan_ip && lan_ip !== '127.0.0.1') {
          setAddress(`${loc.protocol}//${lan_ip}${loc.port ? `:${loc.port}` : ''}`)
        }
      })
      .catch(() => {
        // Offline / unreachable — leave the localhost default; the hint still guides.
      })
  }, [])
  // Tool list comes live from the server, so the catalogue + SKILL.md never drift from the tools.
  const toolsQuery = useMcpTools()
  const groups = toolsQuery.data?.groups ?? []
  const config = clientConfig(address)
  const bundle = groups.length ? skillBundle(address, groups) : []
  const skill = bundle.find((f) => f.name === 'SKILL.md')?.content ?? ''

  const downloadSkill = () => {
    if (bundle.length) saveBlob(buildZip(bundle), 'embedbase.zip')
  }

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">Connection</h2>
        <Card className="flex flex-col gap-4 p-5">
          <Field
            label="Server address"
            htmlFor="mcp-address"
            hint={
              isLocal
                ? "This address only works on this machine; the server couldn't determine a LAN IP for other devices to use."
                : 'Auto-detected from the server. Used to build the endpoint and snippets below.'
            }
          >
            <Input
              id="mcp-address"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              spellCheck={false}
            />
          </Field>
          <ValueRow label="Endpoint" value={`${address}/api/mcp/`} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <span className="text-xs text-ink-faint">Transport</span>
              <span className="font-mono text-[13px] text-ink">HTTP (streamable)</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs text-ink-faint">Auth header</span>
              <span className="font-mono text-[13px] text-ink">Authorization: Bearer …</span>
            </div>
          </div>
          <p className="text-xs text-ink-muted">
            Send an <strong>API key</strong> on every request as{' '}
            <code className="font-mono">Authorization: Bearer &lt;key&gt;</code> or{' '}
            <code className="font-mono">X-API-Key: &lt;key&gt;</code> — the master key, or a{' '}
            <strong>per-user key</strong> for scoped access (each tool respects that user's
            read/write grants). Substitute your own in the snippets below; nothing is embedded here.
          </p>
        </Card>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">Client setup</h2>
        <Card className="flex flex-col gap-3 p-5">
          <p className="text-[13px] text-ink-muted">
            Add this to your MCP client's server config (replace the placeholder key):
          </p>
          <CodeBlock text={config} />
          <div className="flex justify-end">
            <CopyButton text={config} label="Copy config" />
          </div>
        </Card>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">Agent skill</h2>
          {skill && (
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={downloadSkill}>
                <Download className="h-4 w-4" />
                Download .zip
              </Button>
            </div>
          )}
        </div>
        <p className="text-[13px] text-ink-muted">
          A small skill folder (<code className="font-mono">SKILL.md</code> +{' '}
          <code className="font-mono">references/</code>). Unzip it into your agent's skills directory
          (e.g. <code className="font-mono">.claude/skills/embedbase/</code>) — the agent loads{' '}
          <code className="font-mono">SKILL.md</code> up front and opens a reference file only when the
          task needs it, so a read-only session never pulls in the upload rules.
        </p>
        {toolsQuery.isLoading ? (
          <Skeleton className="h-96 w-full rounded-card" />
        ) : skill ? (
          <>
            <Card className="p-0">
              <CodeBlock text={skill} className="max-h-96" />
            </Card>
            <p className="text-xs text-ink-faint">
              Bundled:{' '}
              {bundle
                .filter((f) => f.name !== 'SKILL.md')
                .map((f) => f.name)
                .join(' · ')}
            </p>
          </>
        ) : (
          <p className="text-xs text-ink-muted">
            The skill is unavailable — the tool catalogue could not be loaded.
          </p>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">Tools</h2>
          {groups.length > 0 && (
            <span className="text-xs text-ink-faint">
              {groups.reduce((n, g) => n + g.tools.length, 0)} tools · each respects your grants
            </span>
          )}
        </div>
        {toolsQuery.isError ? (
          <QueryError
            title="Could not load the tool catalogue"
            message={(toolsQuery.error as Error)?.message}
            onRetry={() => void toolsQuery.refetch()}
          />
        ) : toolsQuery.isLoading ? (
          <Skeleton className="h-64 w-full rounded-card" />
        ) : (
          <>
            <Card className="divide-y divide-border p-0">
              {groups.map((g) => (
                <div key={g.group} className="p-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                    {g.group}
                  </p>
                  <div className="space-y-3">
                    {g.tools.map((t) => (
                      <div key={t.name}>
                        <p className="font-mono text-[13px] text-ink">
                          {t.name}
                          <span className="text-ink-faint">{t.signature}</span>
                        </p>
                        <p className="mt-0.5 text-xs text-ink-muted">{t.summary}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </Card>
            <p className="text-xs text-ink-muted">{TAGS_NOTE}</p>
          </>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">REST API reference</h2>
        <Card className="flex flex-wrap items-center justify-between gap-3 p-5">
          <p className="text-[13px] text-ink-muted">
            A standalone OpenAPI reference of just the integration endpoints — search and
            workspace/collection/document access — for the AI to read as the API standard.
          </p>
          <div className="flex gap-2">
            <a href="/api/reference" target="_blank" rel="noreferrer">
              <Button variant="secondary" size="sm">
                <ExternalLink className="h-4 w-4" />
                Swagger UI
              </Button>
            </a>
            <a href="/api/reference.json" target="_blank" rel="noreferrer">
              <Button variant="ghost" size="sm">
                reference.json
              </Button>
            </a>
          </div>
        </Card>
      </section>
    </div>
  )
}

/** A scrollable monospace code block. */
function CodeBlock({ text, className }: { text: string; className?: string }) {
  return (
    <pre
      className={`overflow-auto rounded-control bg-canvas p-3 font-mono text-xs leading-relaxed text-ink ${className ?? ''}`}
    >
      {text}
    </pre>
  )
}
