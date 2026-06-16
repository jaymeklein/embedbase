import { Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/layout/AppLayout'
import { useAuth } from './auth/AuthContext'
import { UnlockScreen } from './auth/UnlockScreen'
import Dashboard from './pages/Dashboard'
import Workspaces from './pages/Workspaces'
import Collections from './pages/Collections'
import Tags from './pages/Tags'
import Documents from './pages/Documents'
import Search from './pages/Search'
import Settings from './pages/Settings'

export default function App() {
  const { isUnlocked } = useAuth()
  if (!isUnlocked) return <UnlockScreen />

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/workspaces" element={<Workspaces />} />
        <Route path="/workspaces/:wsId" element={<Collections />} />
        <Route path="/workspaces/:wsId/tags" element={<Tags />} />
        <Route path="/workspaces/:wsId/collections/:colId" element={<Documents />} />
        <Route path="/search" element={<Search />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}
