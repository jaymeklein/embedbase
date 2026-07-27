import { useEffect, useState } from 'react'
import { Download, ExternalLink } from 'lucide-react'
import { api } from '../../api/client'
import { useMcpTools } from '../../api/hooks'
import type { McpToolGroup } from '../../api/types'
import { Button, Card, CopyButton, Field, Input, QueryError, Skeleton } from '../ui'

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

/** The SKILL.md handed to an agent so it knows how to drive this MCP server. The tool list is the
 *  live catalogue from the server, so it tracks the real surface with no hand-kept copy. */
function skillMd(origin: string, groups: McpToolGroup[]): string {
  const tools = groups
    .map(
      (g) =>
        `### ${g.group}\n${g.tools.map((t) => `- ${t.name}${t.signature} — ${t.summary}`).join('\n')}`,
    )
    .join('\n\n')
  return `---
name: embedbase
description: Drive EmbedBase entirely from chat through its MCP server — search, upload/download, re-ingest, and check ingestion status of documents, and manage workspaces, collections, and tags. Use when the user asks to query their knowledge base, add/remove/re-ingest documents, or organize workspaces, collections, and tags.
---

# EmbedBase MCP

EmbedBase is a local-first document embedding and retrieval system. This skill connects to its MCP
server to run it end to end from chat — search, upload/download, ingestion status, and
workspace/collection/tag management — without opening the console.

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

## Tools

${tools}

${TAGS_NOTE}

## Documents & formats

Only three upload formats are accepted — reject anything else before uploading:

- .pdf
- .md / .markdown
- .txt

When you produce the content yourself, prefer Markdown — structure survives as text, so it embeds and
retrieves best.

### Converting a PDF before upload

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

## REST API reference

A standalone OpenAPI reference of just the integration endpoints (search +
workspace/collection/document access) — not the app's internal endpoints:
- Swagger UI: ${origin}/api/reference
- Raw spec:   ${origin}/api/reference.json

Read the spec to learn the exact request/response shapes behind the tools.

## Typical workflow

1. list_workspaces() to discover workspace + collection ids — never invent them.
2. search_documents(query, [collection_id]) to retrieve relevant chunks; pass a result's chunk_id
   to get_document_chunks for the surrounding context.
3. Upload a file (accepted: .pdf, .md/.markdown, .txt — see "Documents & formats" to convert a PDF
   with tables or images to full text first) in two steps: request_upload(collection_id, filename) →
   PUT the bytes to the returned upload_url → confirm_upload(document_id); then poll
   get_document_status until "done".
4. Cite results by filename; resolve every id from list_workspaces first.
`
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
  const skill = groups.length ? skillMd(address, groups) : ''

  const downloadSkill = () => {
    if (!skill) return
    const url = URL.createObjectURL(new Blob([skill], { type: 'text/markdown' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'SKILL.md'
    a.click()
    URL.revokeObjectURL(url)
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
          <h2 className="text-sm font-semibold text-ink">Agent skill (SKILL.md)</h2>
          {skill && (
            <div className="flex gap-2">
              <CopyButton text={skill} label="Copy" />
              <Button variant="secondary" size="sm" onClick={downloadSkill}>
                <Download className="h-4 w-4" />
                Download
              </Button>
            </div>
          )}
        </div>
        {toolsQuery.isLoading ? (
          <Skeleton className="h-96 w-full rounded-card" />
        ) : skill ? (
          <Card className="p-0">
            <CodeBlock text={skill} className="max-h-96" />
          </Card>
        ) : (
          <p className="text-xs text-ink-muted">
            SKILL.md is unavailable — the tool catalogue could not be loaded.
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
