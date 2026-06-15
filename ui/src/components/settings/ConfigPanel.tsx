import { type ReactNode } from 'react'
import { Info } from 'lucide-react'
import { Card, Field, Input } from '../ui'

/**
 * Disabled scaffold for the runtime-config editor.
 *
 * `GET/PUT /config` and `GET /config/reload-status/{id}` return 501 until
 * Delivery 6 (`api/routers/config.py`), so this form is intentionally inert —
 * it lays out the embedding / vector-store / chunking fields and the three-phase
 * reload-status seam for D6 to wire up. It never fakes a reload.
 */
export function ConfigPanel() {
  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start gap-2 rounded-control border border-accent/30 bg-accent-weak px-3 py-2.5">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        <p className="text-[13px] text-ink-muted">
          Live config reload ships in Delivery 6. These controls are a preview and are disabled for
          now.
        </p>
      </div>

      <fieldset disabled className="flex flex-col gap-5 opacity-60">
        <Section title="Embedding">
          <Field label="Provider" htmlFor="cfg-emb-provider">
            <Input id="cfg-emb-provider" placeholder="sentence_transformers" readOnly />
          </Field>
          <Field label="Model" htmlFor="cfg-emb-model">
            <Input id="cfg-emb-model" placeholder="all-MiniLM-L6-v2" readOnly />
          </Field>
        </Section>

        <Section title="Vector store">
          <Field label="Backend" htmlFor="cfg-vs-backend">
            <Input id="cfg-vs-backend" placeholder="chroma" readOnly />
          </Field>
        </Section>

        <Section title="Chunking">
          <Field label="Chunk size" htmlFor="cfg-chunk-size">
            <Input id="cfg-chunk-size" placeholder="512" readOnly />
          </Field>
          <Field label="Overlap" htmlFor="cfg-chunk-overlap">
            <Input id="cfg-chunk-overlap" placeholder="64" readOnly />
          </Field>
        </Section>
      </fieldset>

      <ReloadStatusSeam />
    </Card>
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

/**
 * Placeholder for the three-phase reload progress.
 *
 * D6 integration seam: wire each step to the phases reported by
 * `GET /config/reload-status/{version_id}` (validate → apply → reload services).
 */
function ReloadStatusSeam() {
  const phases = ['Validate', 'Apply', 'Reload services']
  return (
    <div className="border-t border-border pt-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        Reload progress
      </h3>
      <div className="flex items-center gap-2">
        {phases.map((phase, i) => (
          <div key={phase} className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 rounded-full border border-dashed border-border px-2.5 py-1 text-xs text-ink-faint">
              <span className="font-mono">{i + 1}</span>
              {phase}
            </span>
            {i < phases.length - 1 && <span className="text-ink-faint">→</span>}
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-ink-faint">
        Wired to <code className="font-mono">/config/reload-status/&#123;id&#125;</code> in Delivery 6.
      </p>
    </div>
  )
}
