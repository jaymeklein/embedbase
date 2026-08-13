import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, Cpu, Info, Zap } from 'lucide-react'
import { Trans, useTranslation } from 'react-i18next'
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
import { apiErrorMessage } from '../../i18n/apiError'

/** Runtime config — every section below applies live (API + workers reload). */
export function ConfigPanel() {
  const { t } = useTranslation()
  const { data, isLoading, isError, error, refetch } = useConfig()
  if (isLoading) return <Skeleton className="h-72 w-full rounded-card" />
  if (isError || !data) {
    return (
      <QueryError
        title={t('settings.config.loadError')}
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
  const { t } = useTranslation()
  const storage = config.storage
  const activeName = storage.default
  const activeType = storage.backends?.[activeName]?.type

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          <Trans
            i18nKey="settings.storage.blurb"
            components={{ code: <code />, strong: <strong /> }}
          />
        </p>
      </div>

      <Section title={t('settings.storage.title')}>
        <Field label={t('settings.storage.activeBackend')} htmlFor="storage-backend">
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
  const { t } = useTranslation()
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
        onSuccess: () => toast.success(t('settings.toast.pdfSaved')),
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <Section title={t('settings.parser.title')}>
        <Field label={t('settings.parser.backend')} htmlFor="pdf-backend">
          <Select id="pdf-backend" value={backend} onChange={(e) => setBackend(e.target.value)}>
            <option value="pymupdf">{t('settings.parser.optionPymupdf')}</option>
            <option value="docling">
              {t('settings.parser.optionDocling')}
              {compatible ? '' : t('settings.parser.needsGpuSuffix')}
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
              ? t('settings.parser.gpuOlderThanAmpere', {
                  name: accel.data?.name,
                  capability: accel.data?.capability,
                })
              : t('settings.parser.noGpuDetected')}
            <Trans i18nKey="settings.parser.doclingCpuWarn" components={{ strong: <strong /> }} />
          </p>
        </div>
      )}

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? t('common.saving') : t('settings.parser.save')}
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
  const { t } = useTranslation()
  if (accel.isLoading) {
    return (
      <p className="text-[13px] text-ink-faint sm:col-span-2">
        {t('settings.parser.detectingGpu')}
      </p>
    )
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
        ? t('settings.parser.compatibleGpu', {
            name: accel.data?.name,
            capability: accel.data?.capability,
          })
        : accel.data?.device === 'cuda'
          ? t('settings.parser.gpuBelowAmpere', {
              name: accel.data?.name,
              capability: accel.data?.capability,
            })
          : t('settings.parser.noCompatibleGpu')}{' '}
      {t('settings.parser.recommendedLabel')}{' '}
      <strong className="ml-1">{recommended}</strong>.
    </p>
  )
}

/** Editable form for the embedding section; every other config section round-trips. */
function EmbeddingForm({ config }: { config: AppConfig }) {
  const { t } = useTranslation()
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
        onSuccess: () => toast.success(t('settings.toast.embeddingSaved')),
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-warn/40 bg-warn/10 px-3 py-2.5">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
        <p className="text-[13px] text-ink-muted">
          <Trans i18nKey="settings.embedding.blurb" components={{ strong: <strong /> }} />
        </p>
      </div>

      <Section title={t('settings.embedding.title')}>
        <Field label={t('settings.field.provider')} htmlFor="emb-provider">
          <Select id="emb-provider" value={provider} onChange={(e) => changeProvider(e.target.value)}>
            <option value="ollama">{t('settings.provider.ollamaLocal')}</option>
            <option value="sentence_transformers">
              {t('settings.provider.sentenceTransformers')}
            </option>
            <option value="openai_compat">{t('settings.provider.openaiCompat')}</option>
            <option value="gemini">{t('settings.provider.gemini')}</option>
          </Select>
        </Field>
        {provider === 'ollama' ? (
          <OllamaModelField id="emb-model" model={model} setModel={setModel} baseUrl={baseUrl} />
        ) : (
          <Field
            label={t('settings.field.model')}
            htmlFor="emb-model"
            hint={isGemini ? t('settings.embedding.modelHintGemini') : t('settings.embedding.modelHint')}
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
            label={t('settings.field.baseUrl')}
            htmlFor="emb-base-url"
            hint={
              provider === 'openai_compat'
                ? t('settings.field.baseUrlRequiredOpenrouter')
                : t('settings.field.baseUrlOllamaDefault')
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
          <Field
            label={t('settings.field.apiKey')}
            htmlFor="emb-api-key"
            hint={t('settings.field.apiKeyHint')}
          >
            <Input
              id="emb-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                keyIsSet
                  ? t('settings.field.keySetPlaceholder')
                  : isGemini
                    ? t('settings.field.aiStudioKey')
                    : t('settings.field.apiKey')
              }
            />
          </Field>
        )}
        {isGemini && (
          <Field
            label={t('settings.embedding.outputDim')}
            htmlFor="emb-output-dim"
            hint={t('settings.embedding.outputDimHint')}
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
            label={t('settings.embedding.maxRpm')}
            htmlFor="emb-max-rpm"
            hint={t('settings.embedding.maxRpmHint')}
          >
            <Input
              id="emb-max-rpm"
              type="number"
              min="0"
              value={maxRpm}
              onChange={(e) => setMaxRpm(e.target.value)}
              placeholder={t('settings.embedding.maxRpmPlaceholder')}
            />
          </Field>
        )}
      </Section>

      <div className="flex items-center justify-end gap-3">
        {changesDimensions && (
          <span className="flex items-center gap-1.5 text-[13px] text-warn">
            <AlertTriangle className="h-4 w-4" />
            {t('settings.embedding.reindexRequired')}
          </span>
        )}
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? t('common.saving') : t('settings.embedding.save')}
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
  const { t } = useTranslation()
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
        onSuccess: () => toast.success(t('settings.toast.rerankerSaved')),
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          <Trans i18nKey="settings.reranker.blurb" components={{ strong: <strong /> }} />
        </p>
      </div>

      <Section title={t('settings.reranker.title')}>
        <label className="flex items-center gap-2 text-[13px] text-ink sm:col-span-2">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-5 w-5 accent-accent"
          />
          {t('settings.reranker.enable')}
        </label>
        <Field label={t('settings.field.provider')} htmlFor="rr-provider">
          <Select id="rr-provider" value={provider} onChange={(e) => changeProvider(e.target.value)}>
            <option value="cross_encoder">{t('settings.reranker.providerCrossEncoder')}</option>
            <option value="rerank_api">{t('settings.reranker.providerRerankApi')}</option>
            <option value="llm">{t('settings.reranker.providerLlm')}</option>
          </Select>
        </Field>
        {isLlm && (
          <Field label={t('settings.reranker.llmProvider')} htmlFor="rr-llm-provider">
            <Select
              id="rr-llm-provider"
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value)}
            >
              <option value="gemini">{t('settings.provider.geminiAiStudio')}</option>
              <option value="openai_compat">{t('settings.provider.openaiCompat')}</option>
              <option value="ollama">{t('settings.provider.ollamaLocal')}</option>
            </Select>
          </Field>
        )}
        {isLlmOllama ? (
          <OllamaModelField id="rr-model" model={model} setModel={setModel} baseUrl={baseUrl} />
        ) : (
          <Field
            label={t('settings.field.model')}
            htmlFor="rr-model"
            hint={
              isRerankApi
                ? t('settings.reranker.modelHintRerankApi')
                : isLlm
                  ? isLlmGemini
                    ? t('settings.reranker.modelHintGemini')
                    : t('settings.reranker.modelHintLlm')
                  : t('settings.reranker.modelHintCrossEncoder')
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
                      : t('settings.field.modelIdPlaceholder')
                    : 'cross-encoder/ms-marco-MiniLM-L6-v2'
              }
            />
          </Field>
        )}
        {needsBaseUrl && (
          <Field
            label={t('settings.field.baseUrl')}
            htmlFor="rr-base-url"
            hint={
              isRerankApi
                ? t('settings.reranker.baseUrlHintRerankApi')
                : llmProvider === 'openai_compat'
                  ? t('settings.field.baseUrlRequiredOpenrouter')
                  : t('settings.field.baseUrlOllamaDefault')
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
          <Field
            label={t('settings.field.apiKey')}
            htmlFor="rr-api-key"
            hint={t('settings.field.apiKeyHint')}
          >
            <Input
              id="rr-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                keyIsSet
                  ? t('settings.field.keySetPlaceholder')
                  : isLlmGemini
                    ? t('settings.field.aiStudioKey')
                    : t('settings.field.apiKey')
              }
            />
          </Field>
        )}
        <Field
          label={t('settings.reranker.topN')}
          htmlFor="rr-top-n"
          hint={t('settings.reranker.topNHint')}
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
          {update.isPending ? t('common.saving') : t('settings.reranker.save')}
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
  const { t } = useTranslation()
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
        onSuccess: () => toast.success(t('settings.toast.searchSaved')),
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          <Trans i18nKey="settings.search.blurb" components={{ strong: <strong /> }} />
        </p>
      </div>

      <Section title={t('settings.search.title')}>
        <Field
          label={t('settings.search.expand')}
          htmlFor="search-expand"
          hint={t('settings.search.expandHint', { max: search.max_expand_neighbors })}
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
          {update.isPending ? t('common.saving') : t('settings.search.save')}
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
  const { t } = useTranslation()
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
        onSuccess: () => toast.success(t('settings.toast.mcpSaved')),
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          <Trans i18nKey="settings.mcp.blurb" components={{ code: <code />, strong: <strong /> }} />
        </p>
      </div>

      <Section title={t('settings.mcp.title')}>
        <Field
          label={t('settings.mcp.rateLimit')}
          htmlFor="mcp-rpm"
          hint={t('settings.mcp.rateLimitHint')}
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
          {update.isPending ? t('common.saving') : t('settings.mcp.save')}
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
  const { t } = useTranslation()
  const { data: models, isLoading, isError, error, refetch } = useOllamaModels(baseUrl, true)

  useEffect(() => {
    if (models && models.length > 0 && !models.includes(model)) setModel(models[0])
  }, [models, model, setModel])

  return (
    <Field label={t('settings.field.model')} htmlFor={id} hint={t('settings.field.ollamaModelsHint')}>
      {isError ? (
        <div className="flex items-center gap-2">
          <p className="text-[13px] text-danger">
            {error?.message ?? t('settings.field.ollamaUnreachable')}
          </p>
          <Button variant="ghost" onClick={() => void refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      ) : (
        <Select
          id={id}
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={isLoading || !models?.length}
        >
          {isLoading && <option>{t('settings.field.loadingModels')}</option>}
          {!isLoading && !models?.length && <option>{t('settings.field.noModelsInstalled')}</option>}
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
