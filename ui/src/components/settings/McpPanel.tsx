import { useEffect, useState } from 'react'
import { Copy, Download, ExternalLink } from 'lucide-react'
import { api } from '../../api/client'
import { Button, Card, Field, Input, useToast } from '../ui'

/** The five tools the MCP server exposes (mirrors api/services/mcp/server.py). */
const TOOLS: { name: string; sig: string; desc: string }[] = [
  { name: 'list_workspaces', sig: '()', desc: 'List all workspaces with their collections and document counts. Start here to discover collection ids.' },
  { name: 'search_documents', sig: '(query, collection_ids, top_k=5, hybrid=true, filters?)', desc: 'Hybrid semantic + keyword search across one or more collections.' },
  { name: 'list_documents', sig: '(collection_id)', desc: "List a collection's documents and their ingestion status." },
  { name: 'ingest_document', sig: '(collection_id, file_path)', desc: 'Ingest a server-local file (by path) into a collection.' },
  { name: 'delete_document', sig: '(document_id)', desc: 'Delete a document and enqueue vector + keyword cleanup.' },
]

/** Generic MCP-client config snippet pointing at this deployment's SSE endpoint. */
function clientConfig(origin: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        embedbase: {
          url: `${origin}/mcp/sse`,
          headers: { Authorization: 'Bearer <YOUR_MASTER_API_KEY>' },
        },
      },
    },
    null,
    2,
  )
}

/** The SKILL.md handed to an agent so it knows how to drive this MCP server. */
function skillMd(origin: string): string {
  return `---
name: embedbase
description: Search, ingest, and manage the user's documents through the EmbedBase MCP server. Use when the user asks to query their knowledge base, add or remove documents, or inspect workspaces and collections.
---

# EmbedBase MCP

EmbedBase is a local-first document embedding and retrieval system. This skill
connects to its MCP server to work with the user's document collections.

## Connection

- Transport: SSE (Server-Sent Events)
- URL: ${origin}/mcp/sse
- Auth: send the master API key on every request, either
  - Authorization: Bearer <MASTER_API_KEY>, or
  - X-API-Key: <MASTER_API_KEY>

A missing or wrong key returns 401. The server is rate-limited per key (429 when
the budget is exceeded).

## Tools

${TOOLS.map((t) => `- ${t.name}${t.sig} — ${t.desc}`).join('\n')}

## REST API reference

A standalone OpenAPI reference of just the integration endpoints (search +
workspace/collection/document access) — not the app's internal endpoints:
- Swagger UI: ${origin}/api/reference
- Raw spec:   ${origin}/api/reference.json

Read the spec to learn the exact request/response shapes behind the tools.

## Typical workflow

1. list_workspaces() to discover collection ids — never invent them.
2. search_documents(query, [collection_id]) to retrieve relevant chunks.
3. Cite results by filename; resolve every id from list_workspaces first.
`
}

/** Small copy-to-clipboard button with a success toast. */
function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const toast = useToast()
  const copy = () =>
    navigator.clipboard
      .writeText(text)
      .then(() => toast.success('Copied to clipboard'))
      .catch(() => toast.error('Could not copy'))
  return (
    <Button variant="secondary" size="sm" onClick={copy}>
      <Copy className="h-4 w-4" />
      {label}
    </Button>
  )
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
        <CopyButton text={value} />
      </div>
    </div>
  )
}

/**
 * MCP server tab: connection details, a client-config snippet, the downloadable
 * SKILL.md, the tool catalogue, and links to the live Swagger / OpenAPI reference.
 * The endpoint is derived from the current origin so it's correct wherever the
 * console is served. The master key is shown only as a placeholder — substitute
 * your own; it is never embedded here.
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
  const skill = skillMd(address)
  const config = clientConfig(address)

  const downloadSkill = () => {
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
          <ValueRow label="Endpoint (SSE)" value={`${address}/mcp/sse`} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <span className="text-xs text-ink-faint">Transport</span>
              <span className="font-mono text-[13px] text-ink">SSE</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs text-ink-faint">Auth header</span>
              <span className="font-mono text-[13px] text-ink">Authorization: Bearer …</span>
            </div>
          </div>
          <p className="text-xs text-ink-muted">
            Send your <strong>master API key</strong> on every request as{' '}
            <code className="font-mono">Authorization: Bearer &lt;key&gt;</code> or{' '}
            <code className="font-mono">X-API-Key: &lt;key&gt;</code>. The key lives only in
            this browser — substitute your own in the snippets below.
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
          <div className="flex gap-2">
            <CopyButton text={skill} label="Copy" />
            <Button variant="secondary" size="sm" onClick={downloadSkill}>
              <Download className="h-4 w-4" />
              Download
            </Button>
          </div>
        </div>
        <Card className="p-0">
          <CodeBlock text={skill} className="max-h-96" />
        </Card>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">Tools</h2>
        <Card className="divide-y divide-border p-0">
          {TOOLS.map((t) => (
            <div key={t.name} className="p-4">
              <p className="font-mono text-[13px] text-ink">
                {t.name}
                <span className="text-ink-faint">{t.sig}</span>
              </p>
              <p className="mt-0.5 text-xs text-ink-muted">{t.desc}</p>
            </div>
          ))}
        </Card>
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
