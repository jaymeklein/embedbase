import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, Cpu, Info, Zap } from 'lucide-react'
import {
  SECRET_MASK,
  useAccelerator,
  useConfig,
  useOllamaModels,
  useUpdateConfig,
} from '../../api/hooks'
import type {
  AppConfig,
  EmbeddingConfig,
  MCPConfig,
  RerankerConfig,
  SearchConfig,
} from '../../api/types'
import { Button, Card, Field, Input, QueryError, Select, Skeleton, useToast } from '../ui'

/** Runtime config — every section below applies live (API + workers reload). */
export function ConfigPanel() {
  const { data, isLoading, isError, error, refetch } = useConfig()
  if (isLoading) return <Skeleton className="h-72 w-full rounded-card" />
  if (isError || !data) {
    return (
      <QueryError
        title="Could not load configuration"
        message={error?.message}
        onRetry={() => void refetch()}
      />
    )
  }
  return (
    <div className="flex flex-col gap-5">
      <EmbeddingForm config={data} />
      <RerankerForm config={data} />
      <SearchForm config={data} />
      <ParserForm config={data} />
      <StorageForm config={data} />
      <MCPForm config={data} />
    </div>
  )
}

/**
 * Storage section: a read-only view of the active object-storage backend (selection lives in
 * config.yaml/.env). There is no global retention knob — retention is set **per upload**
 * (`retention_days` 1-30, or permanent). The old `storage.temp_retention_hours` is deprecated and
 * no longer read by any upload path, so the panel no longer offers it.
 */
function StorageForm({ config }: { config: AppConfig }) {
  const storage = config.storage
  const activeName = storage.default
  const activeType = storage.backends?.[activeName]?.type

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          Where uploaded files are stored. The active backend is chosen in{' '}
          <code>config.yaml</code> / <code>.env</code>. Retention is set{' '}
          <strong>per upload</strong> (1–30 days, or kept permanently) on the Documents page —
          there's no global retention setting.
        </p>
      </div>

      <Section title="Object storage">
        <Field label="Active backend" htmlFor="storage-backend">
          <Input
            id="storage-backend"
            value={activeType ? `${activeName} (${activeType})` : activeName}
            readOnly
            disabled
          />
        </Field>
      </Section>
    </Card>
  )
}

/**
 * PDF parsing backend: PyMuPDF (fast, per-page) vs docling (section-aware, tables/OCR).
 * docling is GPU-bound — the choice is steered by detected hardware: pre-selected and
 * unwarned on a compatible (Ampere+) GPU; warned and PyMuPDF-preferred otherwise.
 */
function ParserForm({ config }: { config: AppConfig }) {
  const toast = useToast()
  const update = useUpdateConfig()
  const accel = useAccelerator()
  const compatible = accel.data?.compatible ?? false
  const recommended = compatible ? 'docling' : 'pymupdf'

  // A saved value (pymupdf OR docling) is the user's choice — honour it as-is. Only
  // when the backend was never picked (null) do we pre-select from the GPU.
  const saved = config.parsers?.pdf_backend ?? null
  const [backend, setBackend] = useState(saved ?? 'pymupdf')
  // First-time only: adopt the GPU recommendation once detection resolves. After the
  // user saves, `saved` is non-null and this never fires again — no flip-back.
  useEffect(() => {
    if (accel.data && saved == null) setBackend(recommended)
  }, [accel.data, saved, recommended])

  const doclingOnWeakHw = backend === 'docling' && !compatible

  const save = () => {
    update.mutate(
      { ...config, parsers: { ...config.parsers, pdf_backend: backend } },
      {
        onSuccess: () => toast.success('PDF backend saved. Services are reloading.'),
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <Section title="PDF parsing">
        <Field label="Backend" htmlFor="pdf-backend">
          <Select id="pdf-backend" value={backend} onChange={(e) => setBackend(e.target.value)}>
            <option value="pymupdf">PyMuPDF — fast, one chunk per page</option>
            <option value="docling">
              docling — section-aware + tables/OCR{compatible ? '' : ' (needs a GPU)'}
            </option>
          </Select>
        </Field>
        <AcceleratorNote accel={accel} recommended={recommended} />
      </Section>

      {doclingOnWeakHw && (
        <div className="flex items-start gap-2 rounded-control border border-warn/40 bg-warn/10 px-3 py-2.5">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
          <p className="text-[13px] text-ink-muted">
            {accel.data?.device === 'cuda'
              ? `Your GPU (${accel.data?.name}, capability ${accel.data?.capability}) is older than Ampere — `
              : 'No GPU detected — '}
            docling runs on CPU and is <strong>very slow</strong> (minutes per document, and large
            PDFs can hit the ingestion time limit). PyMuPDF is recommended on this hardware.
          </p>
        </div>
      )}

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save PDF backend'}
        </Button>
      </div>
    </Card>
  )
}

/** Inline hardware line under the backend picker. */
function AcceleratorNote({
  accel,
  recommended,
}: {
  accel: ReturnType<typeof useAccelerator>
  recommended: string
}) {
  if (accel.isLoading) {
    return <p className="text-[13px] text-ink-faint sm:col-span-2">Detecting GPU…</p>
  }
  const compatible = accel.data?.compatible ?? false
  return (
    <p className="flex items-center gap-1.5 text-[13px] text-ink-muted sm:col-span-2">
      {compatible ? (
        <Zap className="h-4 w-4 shrink-0 text-ok" />
      ) : (
        <Cpu className="h-4 w-4 shrink-0 text-ink-faint" />
      )}
      {compatible
        ? `Compatible GPU detected (${accel.data?.name}, capability ${accel.data?.capability}).`
        : accel.data?.device === 'cuda'
          ? `GPU ${accel.data?.name} (capability ${accel.data?.capability}) is below Ampere.`
          : 'No compatible GPU detected.'}{' '}
      Recommended: <strong className="ml-1">{recommended}</strong>.
    </p>
  )
}

/** Editable form for the embedding section; every other config section round-trips. */
function EmbeddingForm({ config }: { config: AppConfig }) {
  const toast = useToast()
  const update = useUpdateConfig()
  const emb = config.embedding

  const [provider, setProvider] = useState(emb.provider)
  const [model, setModel] = useState(emb.model)
  const [baseUrl, setBaseUrl] = useState(emb.base_url ?? '')
  const [apiKey, setApiKey] = useState('') // blank = keep existing key (when set)
  const [outputDim, setOutputDim] = useState(
    emb.output_dimensionality != null ? String(emb.output_dimensionality) : '',
  )
  const [maxRpm, setMaxRpm] = useState(emb.max_rpm ? String(emb.max_rpm) : '')

  // Switching provider invalidates the current model: clear it (the user types the
  // new one; Ollama's picker auto-selects the first installed model).
  const changeProvider = (next: string) => {
    setProvider(next)
    setModel('')
  }

  const keyIsSet = emb.api_key === SECRET_MASK
  const needsKey = provider === 'openai_compat' || provider === 'gemini'
  // Gemini's endpoint is fixed — no Base URL field (override lives in config.yaml).
  const needsBaseUrl = provider === 'ollama' || provider === 'openai_compat'
  const isGemini = provider === 'gemini'

  const changesDimensions =
    provider !== emb.provider ||
    model !== emb.model ||
    (isGemini && (Number(outputDim) || null) !== emb.output_dimensionality)

  const save = () => {
    const nextKey = apiKey.trim() || (keyIsSet ? SECRET_MASK : '')
    const embedding: EmbeddingConfig = {
      provider,
      model,
      batch_size: emb.batch_size,
      base_url: needsBaseUrl ? baseUrl.trim() || null : null,
      api_key: needsKey ? nextKey : '',
      concurrency: emb.concurrency,
      output_dimensionality: isGemini ? Number(outputDim) || null : null,
      max_rpm: Math.max(0, Number(maxRpm) || 0),
    }
    update.mutate(
      { ...config, embedding },
      {
        onSuccess: () => toast.success('Embedding config saved. Services are reloading.'),
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-warn/40 bg-warn/10 px-3 py-2.5">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
        <p className="text-[13px] text-ink-muted">
          The embedding model turns documents into vectors. <strong>Changing the provider,
            model, or output dimensions changes the vector size</strong> — existing collections
          must be re-indexed or search will break. Changing only an API key is safe.
        </p>
      </div>

      <Section title="Embedding model">
        <Field label="Provider" htmlFor="emb-provider">
          <Select id="emb-provider" value={provider} onChange={(e) => changeProvider(e.target.value)}>
            <option value="ollama">Ollama (local)</option>
            <option value="sentence_transformers">Sentence-Transformers (in-process)</option>
            <option value="openai_compat">OpenAI-compatible</option>
            <option value="gemini">Google Gemini</option>
          </Select>
        </Field>
        {provider === 'ollama' ? (
          <OllamaModelField id="emb-model" model={model} setModel={setModel} baseUrl={baseUrl} />
        ) : (
          <Field
            label="Model"
            htmlFor="emb-model"
            hint={isGemini ? 'e.g. gemini-embedding-001' : 'the exact model id on this server'}
          >
            <Input
              id="emb-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={isGemini ? 'gemini-embedding-001' : 'nvidia/llama-nemotron-embed-vl-1b-v2:free'}
            />
          </Field>
        )}
        {needsBaseUrl && (
          <Field
            label="Base URL"
            htmlFor="emb-base-url"
            hint={
              provider === 'openai_compat'
                ? 'required — e.g. https://openrouter.ai/api'
                : 'blank uses the Ollama default'
            }
          >
            <Input
              id="emb-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={
                provider === 'openai_compat'
                  ? 'https://openrouter.ai/api'
                  : 'http://host.docker.internal:11434'
              }
            />
          </Field>
        )}
        {needsKey && (
          <Field label="API key" htmlFor="emb-api-key" hint="Write-only; never shown after saving.">
            <Input
              id="emb-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                keyIsSet ? 'key set — leave blank to keep' : isGemini ? 'AI Studio key' : 'API key'
              }
            />
          </Field>
        )}
        {isGemini && (
          <Field
            label="Output dimensions"
            htmlFor="emb-output-dim"
            hint="optional; blank = full 3072"
          >
            <Input
              id="emb-output-dim"
              type="number"
              min="1"
              value={outputDim}
              onChange={(e) => setOutputDim(e.target.value)}
              placeholder="768"
            />
          </Field>
        )}
        {provider !== 'sentence_transformers' && (
          <Field
            label="Max requests / min"
            htmlFor="emb-max-rpm"
            hint="throttle embeds so bulk ingestion stays under the provider's quota (each text = one request); blank or 0 = unlimited"
          >
            <Input
              id="emb-max-rpm"
              type="number"
              min="0"
              value={maxRpm}
              onChange={(e) => setMaxRpm(e.target.value)}
              placeholder="0 (unlimited)"
            />
          </Field>
        )}
      </Section>

      <div className="flex items-center justify-end gap-3">
        {changesDimensions && (
          <span className="flex items-center gap-1.5 text-[13px] text-warn">
            <AlertTriangle className="h-4 w-4" />
            Re-index required after saving
          </span>
        )}
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save embedding config'}
        </Button>
      </div>
    </Card>
  )
}

/**
 * Reranker (second-stage) config: reorders retrieved candidates by true query relevance before
 * the top_k cut. Provider-pluggable, mirroring the embedding form — a local cross-encoder, a
 * hosted /rerank API (Cohere/Jina/Voyage), or an LLM-as-reranker (incl. Gemini / AI Studio).
 * Optional + graceful: a load or remote failure degrades to RRF-only ranking, never a 500.
 */
function RerankerForm({ config }: { config: AppConfig }) {
  const toast = useToast()
  const update = useUpdateConfig()
  const rr = config.reranker

  const [enabled, setEnabled] = useState(rr.enabled)
  const [provider, setProvider] = useState(rr.provider)
  const [model, setModel] = useState(rr.model)
  const [llmProvider, setLlmProvider] = useState(rr.llm_provider)
  const [baseUrl, setBaseUrl] = useState(rr.base_url ?? '')
  const [apiKey, setApiKey] = useState('') // blank = keep existing key (when set)
  const [topN, setTopN] = useState(String(rr.top_n))

  const keyIsSet = rr.api_key === SECRET_MASK
  const isRerankApi = provider === 'rerank_api'
  const isLlm = provider === 'llm'
  const isLlmOllama = isLlm && llmProvider === 'ollama'
  const isLlmGemini = isLlm && llmProvider === 'gemini'

  // rerank_api always needs a key; the LLM reranker needs one unless it's local Ollama.
  const needsKey = isRerankApi || (isLlm && llmProvider !== 'ollama')
  // Base URL: rerank_api's full endpoint, or the LLM base for Ollama / OpenAI-compatible.
  // Gemini's endpoint is fixed (override lives in config.yaml), so no field there.
  const needsBaseUrl =
    isRerankApi || (isLlm && (llmProvider === 'ollama' || llmProvider === 'openai_compat'))

  // Switching provider invalidates the current model (a cross-encoder id ≠ an LLM/rerank id).
  const changeProvider = (next: string) => {
    setProvider(next)
    setModel('')
  }

  const save = () => {
    const nextKey = apiKey.trim() || (keyIsSet ? SECRET_MASK : '')
    const reranker: RerankerConfig = {
      enabled,
      provider,
      model,
      llm_provider: llmProvider,
      base_url: needsBaseUrl ? baseUrl.trim() || null : null,
      api_key: needsKey ? nextKey : '',
      top_n: Math.max(1, Number(topN) || 50),
      timeout_seconds: rr.timeout_seconds,
    }
    update.mutate(
      { ...config, reranker },
      {
        onSuccess: () => toast.success('Reranker config saved. Services are reloading.'),
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          The reranker reorders retrieved chunks by true query relevance before the final cut — the
          biggest precision win. It's <strong>optional and safe</strong>: if the model or a remote
          provider is unavailable, search falls back to its normal ranking.
        </p>
      </div>

      <Section title="Reranker">
        <label className="flex items-center gap-2 text-[13px] text-ink sm:col-span-2">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-5 w-5 accent-accent"
          />
          Enable reranking
        </label>
        <Field label="Provider" htmlFor="rr-provider">
          <Select id="rr-provider" value={provider} onChange={(e) => changeProvider(e.target.value)}>
            <option value="cross_encoder">Cross-encoder (local, no key)</option>
            <option value="rerank_api">Hosted rerank API (Cohere / Jina / Voyage)</option>
            <option value="llm">LLM-as-reranker</option>
          </Select>
        </Field>
        {isLlm && (
          <Field label="LLM provider" htmlFor="rr-llm-provider">
            <Select
              id="rr-llm-provider"
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value)}
            >
              <option value="gemini">Google Gemini (AI Studio)</option>
              <option value="openai_compat">OpenAI-compatible</option>
              <option value="ollama">Ollama (local)</option>
            </Select>
          </Field>
        )}
        {isLlmOllama ? (
          <OllamaModelField id="rr-model" model={model} setModel={setModel} baseUrl={baseUrl} />
        ) : (
          <Field
            label="Model"
            htmlFor="rr-model"
            hint={
              isRerankApi
                ? 'e.g. rerank-v3.5 (Cohere), jina-reranker-v2 (Jina)'
                : isLlm
                  ? isLlmGemini
                    ? 'e.g. gemini-2.5-flash'
                    : 'the exact chat model id'
                  : 'a HuggingFace cross-encoder id; the baked default is offline-safe'
            }
          >
            <Input
              id="rr-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={
                isRerankApi
                  ? 'rerank-v3.5'
                  : isLlm
                    ? isLlmGemini
                      ? 'gemini-2.5-flash'
                      : 'model id'
                    : 'cross-encoder/ms-marco-MiniLM-L6-v2'
              }
            />
          </Field>
        )}
        {needsBaseUrl && (
          <Field
            label="Base URL"
            htmlFor="rr-base-url"
            hint={
              isRerankApi
                ? 'required — the full /rerank endpoint'
                : llmProvider === 'openai_compat'
                  ? 'required — e.g. https://openrouter.ai/api'
                  : 'blank uses the Ollama default'
            }
          >
            <Input
              id="rr-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={
                isRerankApi
                  ? 'https://api.cohere.com/v2/rerank'
                  : llmProvider === 'openai_compat'
                    ? 'https://openrouter.ai/api'
                    : 'http://host.docker.internal:11434'
              }
            />
          </Field>
        )}
        {needsKey && (
          <Field label="API key" htmlFor="rr-api-key" hint="Write-only; never shown after saving.">
            <Input
              id="rr-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                keyIsSet ? 'key set — leave blank to keep' : isLlmGemini ? 'AI Studio key' : 'API key'
              }
            />
          </Field>
        )}
        <Field
          label="Candidates scored (top_n)"
          htmlFor="rr-top-n"
          hint="max candidates the reranker scores per collection"
        >
          <Input
            id="rr-top-n"
            type="number"
            min="1"
            value={topN}
            onChange={(e) => setTopN(e.target.value)}
          />
        </Field>
      </Section>

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save reranker config'}
        </Button>
      </div>
    </Card>
  )
}

/**
 * Search / retrieval config. The one live-editable knob is A2 adjacency expansion — how many
 * chunks to pull on each side of a hit and merge into a span, so a context that spilled past
 * top_k onto the next page(s) comes back whole. 0 disables it; the other search knobs round-trip.
 */
function SearchForm({ config }: { config: AppConfig }) {
  const toast = useToast()
  const update = useUpdateConfig()
  const search = config.search

  const [expand, setExpand] = useState(String(search.expand_neighbors))

  const save = () => {
    // Round before clamping: expand_neighbors is a strict int on the backend, so a fractional
    // entry (the number input allows "2.7") would 422 the PUT — coerce to the nearest whole count.
    const whole = Math.round(Number(expand) || 0)
    const clamped = Math.max(0, Math.min(whole, search.max_expand_neighbors))
    const next: SearchConfig = { ...search, expand_neighbors: clamped }
    update.mutate(
      { ...config, search: next },
      {
        onSuccess: () => toast.success('Search config saved. Services are reloading.'),
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          Adjacency expansion pulls the chunks next to each result and merges them into one span, so
          a section that runs across pages comes back whole instead of cut at the top-K boundary.
          It's a plain lookup — no extra model calls — and <strong>safe</strong>: if it can't fetch,
          search returns the un-expanded results.
        </p>
      </div>

      <Section title="Retrieval">
        <Field
          label="Adjacency expansion (neighbours)"
          htmlFor="search-expand"
          hint={`chunks pulled on each side of a hit; 0 = off, max ${search.max_expand_neighbors}`}
        >
          <Input
            id="search-expand"
            type="number"
            min="0"
            max={String(search.max_expand_neighbors)}
            value={expand}
            onChange={(e) => setExpand(e.target.value)}
          />
        </Field>
      </Section>

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save search config'}
        </Button>
      </div>
    </Card>
  )
}

/**
 * MCP server config. The one editable knob is the per-key request rate limit on the mounted
 * `/api/mcp` endpoint (the 61st request in a minute → 429 by default). Applies live, like the
 * sections above: the limiter reads `mcp.rate_limit_rpm` from the live config on every request,
 * so a save binds from the next MCP call. `enabled` and `max_results` round-trip untouched.
 */
function MCPForm({ config }: { config: AppConfig }) {
  const toast = useToast()
  const update = useUpdateConfig()
  const mcp = config.mcp

  const [rpm, setRpm] = useState(String(mcp.rate_limit_rpm))

  const save = () => {
    // The backend takes a strict int >= 1 and 422s anything else. Blank (or unparseable) reverts
    // to the saved value; everything else is rounded and floored at 1. Test the entry for blank
    // up front rather than leaning on `Number(x) || fallback` — `Number('0')` is falsy, so that
    // idiom would divert 0 to the fallback and never floor it.
    const entered = rpm.trim()
    const parsed = Math.round(Number(entered))
    const rateLimitRpm =
      entered === '' || !Number.isFinite(parsed) ? mcp.rate_limit_rpm : Math.max(1, parsed)
    const next: MCPConfig = { ...mcp, rate_limit_rpm: rateLimitRpm }
    setRpm(String(rateLimitRpm)) // show what was actually saved, not the raw entry
    update.mutate(
      { ...config, mcp: next },
      {
        onSuccess: () => toast.success('MCP config saved. Services are reloading.'),
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          The MCP endpoint (<code>/api/mcp</code>) throttles each API key to this many requests per
          minute; the next request in the same minute returns <code>429</code>. Each key gets its own
          budget, and the new limit <strong>applies to the next MCP request</strong>
        </p>
      </div>

      <Section title="MCP server">
        <Field
          label="Rate limit (requests / min per key)"
          htmlFor="mcp-rpm"
          hint="per API key on /api/mcp; the next request in the same minute returns 429"
        >
          <Input
            id="mcp-rpm"
            type="number"
            min="1"
            value={rpm}
            onChange={(e) => setRpm(e.target.value)}
          />
        </Field>
      </Section>

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save MCP config'}
        </Button>
      </div>
    </Card>
  )
}

/**
 * Model picker for Ollama: a Select populated from the server's installed models.
 * No free-text entry — if a model isn't installed it can't be chosen. Auto-selects
 * the first model when the current value isn't among those installed.
 */
function OllamaModelField({
  id,
  model,
  setModel,
  baseUrl,
}: {
  id: string
  model: string
  setModel: (m: string) => void
  baseUrl: string
}) {
  const { data: models, isLoading, isError, error, refetch } = useOllamaModels(baseUrl, true)

  useEffect(() => {
    if (models && models.length > 0 && !models.includes(model)) setModel(models[0])
  }, [models, model, setModel])

  return (
    <Field label="Model" htmlFor={id} hint="Installed Ollama models">
      {isError ? (
        <div className="flex items-center gap-2">
          <p className="text-[13px] text-danger">{error?.message ?? 'Could not reach Ollama'}</p>
          <Button variant="ghost" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      ) : (
        <Select
          id={id}
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={isLoading || !models?.length}
        >
          {isLoading && <option>Loading models…</option>}
          {!isLoading && !models?.length && <option>No models installed</option>}
          {models?.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      )}
    </Field>
  )
}

/** A titled two-column group of fields. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">{title}</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div>
    </div>
  )
}
