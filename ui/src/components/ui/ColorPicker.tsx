import { cn } from '../../lib/cn'

/** Curated, muted swatch set — deliberately not a full color wheel. */
export const SWATCHES = [
  '#5B6B7A',
  '#2B5CE6',
  '#1E8E5A',
  '#B26B00',
  '#C0382B',
  '#7C3AED',
  '#0E7490',
  '#9A5B2E',
] as const

export function ColorPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (color: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {SWATCHES.map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => onChange(c)}
          aria-label={`Color ${c}`}
          aria-pressed={value === c}
          className={cn(
            'h-7 w-7 rounded-full border transition-transform duration-150 hover:scale-110',
            value === c
              ? 'border-ink ring-2 ring-accent ring-offset-2 ring-offset-surface'
              : 'border-border',
          )}
          style={{ backgroundColor: c }}
        />
      ))}
    </div>
  )
}
