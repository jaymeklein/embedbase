import { NavLink } from 'react-router-dom'
import { DatabaseZap, FolderKanban, LayoutDashboard, ListChecks, Search, Settings, Workflow, type LucideIcon } from 'lucide-react'
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
]

// Grouped under the "Admin" heading in the sidebar.
const ADMIN_ITEMS: NavItem[] = [
  { to: '/indexing', label: 'Indexing', icon: DatabaseZap },
  { to: '/ingestion-queue', label: 'Ingestion Queue', icon: ListChecks },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function NavItemLink({ to, label, icon: Icon, end }: NavItem) {
  return (
    <NavLink
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
      <Icon className="h-5 w-5" />
      {label}
    </NavLink>
  )
}

export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2 px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-[13px] font-bold text-white">
          e
        </div>
        <span className="font-semibold tracking-tight text-ink">EmbedBase</span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3 py-2">
        {ITEMS.map((item) => (
          <NavItemLink key={item.to} {...item} />
        ))}
        <div className="mt-4 px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
          Admin
        </div>
        {ADMIN_ITEMS.map((item) => (
          <NavItemLink key={item.to} {...item} />
        ))}
      </nav>
    </aside>
  )
}
