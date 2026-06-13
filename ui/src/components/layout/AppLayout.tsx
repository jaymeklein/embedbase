import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'

/** Persistent shell: left sidebar + top bar + scrollable content outlet. */
export function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-8 py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
