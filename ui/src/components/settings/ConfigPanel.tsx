import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, Cpu, Info, Zap } from 'lucide-react'
import {
  SECRET_MASK,
  useAccelerator,
  useConfig,
  useOllamaModels,
  useTestOllama,
  useUpdateConfig,
} from '../../api/hooks'
import type { AppConfig, EmbeddingConfig, TaggingConfig } from '../../api/types'
import { Button, Card, Field, Input, QueryError, Select, Skeleton, useToast } from '../ui'

/** Runtime config: embedding model + AI tag-suggester. Both apply live (API + workers reload). */
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
      <ParserForm config={data} />
      <TaggingForm config={data} />
    </div>
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
          <OllamaModelField model={model} setModel={setModel} baseUrl={baseUrl} />
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

/** Editable form for the tagging section; every other config section round-trips. */
function TaggingForm({ config }: { config: AppConfig }) {
  const toast = useToast()
  const update = useUpdateConfig()
  const testOllama = useTestOllama()
  const sug = config.tagging.suggester

  const [provider, setProvider] = useState(sug.provider)
  const [model, setModel] = useState(sug.model)
  const [baseUrl, setBaseUrl] = useState(sug.base_url ?? '')
  const [apiKey, setApiKey] = useState('') // blank = keep existing key (when set)
  const [maxTags, setMaxTags] = useState(String(sug.max_tags))
  const [minConfidence, setMinConfidence] = useState(String(sug.min_confidence))
  const [autoTag, setAutoTag] = useState(config.tagging.auto_tag_on_ingest)

  // Tag suggestion is LLM-only; tagging is otherwise manual. Only the provider
  // (local Ollama vs OpenAI-compatible) varies — there is no local backend.
  const isOpenAI = provider === 'openai_compat'
  const keyIsSet = sug.api_key === SECRET_MASK

  // Feedback is shown inline next to the button (see below), not as a toast.
  const testConnection = () => testOllama.mutate(baseUrl)

  const save = () => {
    // Blank key + already-set → echo the mask so the backend preserves it.
    const nextKey = apiKey.trim() || (keyIsSet ? SECRET_MASK : '')
    const tagging: TaggingConfig = {
      auto_tag_on_ingest: autoTag,
      suggester: {
        backend: 'llm',
        provider,
        model,
        base_url: baseUrl.trim() || null,
        api_key: nextKey,
        max_tags: Number(maxTags) || 8,
        min_confidence: Number(minConfidence),
      },
    }
    update.mutate(
      { ...config, tagging },
      {
        onSuccess: () => toast.success('Configuration saved. Services are reloading.'),
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          Tags are suggested by an AI model. <strong>Ollama</strong> runs locally and needs no key;
          <strong> OpenAI-compatible</strong> (OpenRouter, etc.) needs a base URL and key. Saving
          applies live — the API and workers reload.
        </p>
      </div>

      <Section title="AI tag suggester">
        <Field label="Provider" htmlFor="cfg-provider">
          <Select id="cfg-provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="ollama">Ollama (local)</option>
            <option value="openai_compat">OpenAI-compatible (OpenRouter, …)</option>
          </Select>
        </Field>
        {isOpenAI && (
          <Field label="Model" htmlFor="cfg-model">
            <Input
              id="cfg-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="meta-llama/llama-3.1-8b-instruct"
            />
          </Field>
        )}
        {!isOpenAI && (
          <OllamaModelField model={model} setModel={setModel} baseUrl={baseUrl} />
        )}
        <Field
          label="Base URL"
          htmlFor="cfg-base-url"
          hint={isOpenAI ? 'e.g. https://openrouter.ai/api/v1' : 'blank uses the Ollama default'}
        >
          <Input
            id="cfg-base-url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={
              isOpenAI ? 'https://openrouter.ai/api/v1' : 'http://host.docker.internal:11434'
            }
          />
        </Field>
        {isOpenAI && (
          <Field label="API key" htmlFor="cfg-api-key" hint="Write-only; never shown after saving.">
            <Input
              id="cfg-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={keyIsSet ? 'key set — leave blank to keep' : 'sk-or-…'}
            />
          </Field>
        )}
        {!isOpenAI && (
          <div className="flex items-center gap-3 sm:col-span-2">
            <Button variant="secondary" onClick={testConnection} loading={testOllama.isPending}>
              Test connection
            </Button>
            {testOllama.isPending ? (
              <span className="text-[13px] text-ink-muted">Checking…</span>
            ) : testOllama.isSuccess ? (
              <span className="flex items-center gap-1.5 text-[13px] text-ok">
                <span className="h-1.5 w-1.5 rounded-full bg-ok" />
                Reachable — {testOllama.data.length} model{testOllama.data.length === 1 ? '' : 's'}
              </span>
            ) : testOllama.isError ? (
              <span className="flex items-center gap-1.5 text-[13px] text-err">
                <span className="h-1.5 w-1.5 rounded-full bg-err" />
                {testOllama.error.message}
              </span>
            ) : (
              <span className="text-[13px] text-ink-muted">
                Check that Ollama is reachable at the base URL above.
              </span>
            )}
          </div>
        )}
      </Section>

      <Section title="Auto-tagging at ingestion">
        <Field label="Max tags" htmlFor="cfg-max-tags">
          <Input
            id="cfg-max-tags"
            type="number"
            min="1"
            value={maxTags}
            onChange={(e) => setMaxTags(e.target.value)}
          />
        </Field>
        <Field
          label="Min confidence"
          htmlFor="cfg-min-conf"
          hint="0–1; only tags scoring at least this are auto-applied"
        >
          <Input
            id="cfg-min-conf"
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={minConfidence}
            onChange={(e) => setMinConfidence(e.target.value)}
          />
        </Field>
        <label className="flex items-center gap-2 text-[13px] text-ink sm:col-span-2">
          <input
            type="checkbox"
            checked={autoTag}
            onChange={(e) => setAutoTag(e.target.checked)}
            className="h-5 w-5 accent-accent"
          />
          Auto-tag documents with the AI suggester at ingestion
        </label>
      </Section>

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save configuration'}
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
  model,
  setModel,
  baseUrl,
}: {
  model: string
  setModel: (m: string) => void
  baseUrl: string
}) {
  const { data: models, isLoading, isError, error, refetch } = useOllamaModels(baseUrl, true)

  useEffect(() => {
    if (models && models.length > 0 && !models.includes(model)) setModel(models[0])
  }, [models, model, setModel])

  return (
    <Field label="Model" htmlFor="cfg-model" hint="Installed Ollama models">
      {isError ? (
        <div className="flex items-center gap-2">
          <p className="text-[13px] text-danger">{error?.message ?? 'Could not reach Ollama'}</p>
          <Button variant="ghost" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      ) : (
        <Select
          id="cfg-model"
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
