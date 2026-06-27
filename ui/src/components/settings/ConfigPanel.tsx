import { useEffect, useState, type ReactNode } from 'react'
import { Info } from 'lucide-react'
import { SECRET_MASK, useConfig, useOllamaModels, useTestOllama, useUpdateConfig } from '../../api/hooks'
import type { AppConfig, TaggingConfig } from '../../api/types'
import { Button, Card, Field, Input, QueryError, Select, Skeleton, useToast } from '../ui'

/** Runtime AI tag-suggester config: switch keyword / Ollama / OpenAI-compatible. */
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
  return <TaggingForm config={data} />
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

  // Suggestions are always AI/LLM — the keyword backend is unreliable, so it is no
  // longer selectable. Only the provider (local Ollama vs OpenAI-compatible) varies.
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
