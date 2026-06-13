import { Route, Routes } from 'react-router-dom'
import { Hammer } from 'lucide-react'
import { AppLayout } from './components/layout/AppLayout'
import { EmptyState } from './components/ui'
import { useAuth } from './auth/AuthContext'
import { UnlockScreen } from './auth/UnlockScreen'
import Dashboard from './pages/Dashboard'
import Workspaces from './pages/Workspaces'
import Collections from './pages/Collections'
import Documents from './pages/Documents'

/** Placeholder screen until each page lands in a later Delivery 5 phase. */
function ComingSoon({ name }: { name: string }) {
  return (
    <div className="animate-fade-in">
      <h1 className="mb-1 text-xl font-semibold tracking-tight text-ink">{name}</h1>
      <p className="mb-6 text-[13px] text-ink-muted">Part of Delivery 5.</p>
      <EmptyState
        icon={<Hammer className="h-6 w-6" />}
        title={`${name} is coming soon`}
        description="This screen will be implemented in an upcoming Delivery 5 phase."
      />
    </div>
  )
}

export default function App() {
  const { isUnlocked } = useAuth()
  if (!isUnlocked) return <UnlockScreen />

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/workspaces" element={<Workspaces />} />
        <Route path="/workspaces/:wsId" element={<Collections />} />
        <Route path="/workspaces/:wsId/collections/:colId" element={<Documents />} />
        <Route path="/search" element={<ComingSoon name="Search" />} />
        <Route path="/settings" element={<ComingSoon name="Settings" />} />
      </Route>
    </Routes>
  )
}
