import { Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/layout/AppLayout'
import { Spinner } from './components/ui'
import { useAuth } from './auth/AuthContext'
import { ChangePasswordScreen } from './auth/ChangePasswordScreen'
import { LoginScreen } from './auth/LoginScreen'
import Dashboard from './pages/Dashboard'
import Workspaces from './pages/Workspaces'
import Collections from './pages/Collections'
import Tags from './pages/Tags'
import Documents from './pages/Documents'
import Graph from './pages/Graph'
import Search from './pages/Search'
import Indexing from './pages/Indexing'
import IngestionQueue from './pages/IngestionQueue'
import Users from './pages/Users'
import Settings from './pages/Settings'

export default function App() {
  const { isAuthed, mustChangePassword, isAdmin, canManageTags, hydrating } = useAuth()
  if (hydrating) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas">
        <Spinner className="h-6 w-6" />
      </div>
    )
  }
  if (!isAuthed) return <LoginScreen />
  if (mustChangePassword) return <ChangePasswordScreen />

  return (
    <Routes>
      <Route element={<AppLayout />}>
        {/* Data plane — any signed-in user, scoped to their grants by the backend. The
            ingestion views show only the caller's permitted collections' jobs/coverage;
            their retry actions are gated to admins inside each page. */}
        <Route path="/workspaces" element={<Workspaces />} />
        <Route path="/workspaces/:wsId" element={<Collections />} />
        <Route path="/workspaces/:wsId/collections/:colId" element={<Documents />} />
        <Route path="/search" element={<Search />} />
        <Route path="/indexing" element={<Indexing />} />
        <Route path="/ingestion-queue" element={<IngestionQueue />} />
        {/* Tag management — admins or a user holding the `manage_tags` capability.
            Kept out of the admin-only block below so a granted non-admin can reach it. */}
        {(isAdmin || canManageTags) && (
          <Route path="/workspaces/:wsId/tags" element={<Tags />} />
        )}
        {/* Admin-only management plane (master key or an admin session). Routes are
            omitted for non-admins, so a direct hit falls through to the catch-all. */}
        {isAdmin ? (
          <>
            <Route path="/" element={<Dashboard />} />
            <Route path="/graph" element={<Graph />} />
            <Route path="/users" element={<Users />} />
            <Route path="/settings" element={<Settings />} />
          </>
        ) : (
          <Route path="/" element={<Navigate to="/workspaces" replace />} />
        )}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
