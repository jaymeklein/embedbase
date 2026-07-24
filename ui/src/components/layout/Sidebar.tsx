import { NavLink } from 'react-router-dom'
import { DatabaseZap, FolderKanban, LayoutDashboard, ListChecks, Search, Settings, Users, Workflow, type LucideIcon } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useAuth } from '../../auth/AuthContext'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

// Admins see the full console; a non-admin sees only the data plane they're scoped
// to (browse their permitted workspaces + search + the ingestion views below).
const ADMIN_MAIN: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/workspaces', label: 'Workspaces', icon: FolderKanban },
  { to: '/graph', label: 'Graph', icon: Workflow },
  { to: '/search', label: 'Search', icon: Search },
]

const USER_MAIN: NavItem[] = [
  { to: '/workspaces', label: 'Workspaces', icon: FolderKanban },
  { to: '/search', label: 'Search', icon: Search },
]

// Ingestion views — everyone sees them; the backend scopes their contents to the
// caller's grants (a non-admin sees only their permitted collections' jobs/coverage).
const INGESTION_ITEMS: NavItem[] = [
  { to: '/indexing', label: 'Indexing', icon: DatabaseZap },
  { to: '/ingestion-queue', label: 'Ingestion Queue', icon: ListChecks },
]

// Grouped under the "Admin" heading in the sidebar (admins only).
const ADMIN_ITEMS: NavItem[] = [
  { to: '/users', label: 'Users', icon: Users },
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
  const { isAdmin } = useAuth()
  const mainItems = [...(isAdmin ? ADMIN_MAIN : USER_MAIN), ...INGESTION_ITEMS]
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2 px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-[13px] font-bold text-white">
          e
        </div>
        <span className="font-semibold tracking-tight text-ink">EmbedBase</span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3 py-2">
        {mainItems.map((item) => (
          <NavItemLink key={item.to} {...item} />
        ))}
        {isAdmin && (
          <>
            <div className="mt-4 px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Admin
            </div>
            {ADMIN_ITEMS.map((item) => (
              <NavItemLink key={item.to} {...item} />
            ))}
          </>
        )}
      </nav>
    </aside>
  )
}
