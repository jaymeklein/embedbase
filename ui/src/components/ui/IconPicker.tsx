import {
  BookOpen,
  Box,
  Code2,
  Database,
  FileText,
  Folder,
  Hash,
  Layers,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '../../lib/cn'

/** Named subset of lucide icons usable for workspaces / collections. */
export const ICONS: Record<string, LucideIcon> = {
  folder: Folder,
  file: FileText,
  database: Database,
  box: Box,
  book: BookOpen,
  code: Code2,
  layers: Layers,
  hash: Hash,
}

export function IconPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (name: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {Object.entries(ICONS).map(([name, Icon]) => (
        <button
          key={name}
          type="button"
          onClick={() => onChange(name)}
          aria-label={`Icon ${name}`}
          aria-pressed={value === name}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-control border transition-colors duration-150',
            value === name
              ? 'border-accent bg-accent-weak text-accent'
              : 'border-border text-ink-muted hover:text-ink',
          )}
        >
          <Icon className="h-4 w-4" />
        </button>
      ))}
    </div>
  )
}

/** Render a named entity icon, falling back to a folder. */
export function EntityIcon({ name, className }: { name?: string | null; className?: string }) {
  const Icon = (name && ICONS[name]) || Folder
  return <Icon className={className} />
}
