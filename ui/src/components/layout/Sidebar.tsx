import { NavLink } from 'react-router-dom'
import { FolderKanban, LayoutDashboard, Search, Settings, Workflow, type LucideIcon } from 'lucide-react'
import { cn } from '../../lib/cn'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

const ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/workspaces', label: 'Workspaces', icon: FolderKanban },
  { to: '/graph', label: 'Graph', icon: Workflow },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2 px-5">
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[13px] font-bold text-white">
          e
        </div>
        <span className="font-semibold tracking-tight text-ink">EmbedBase</span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3 py-2">
        {ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-control px-3 py-2 text-[13px] font-medium transition-colors duration-150',
                isActive
                  ? 'bg-accent-weak text-accent'
                  : 'text-ink-muted hover:bg-canvas hover:text-ink',
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
