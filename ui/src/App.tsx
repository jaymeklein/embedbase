import { Routes, Route } from 'react-router-dom'

// Pages implemented in Delivery 5
const Placeholder = ({ name }: { name: string }) => (
  <div className="p-8 text-gray-600">
    <h1 className="text-2xl font-semibold mb-2">{name}</h1>
    <p>Implemented in Delivery 5.</p>
  </div>
)

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <Routes>
        <Route path="/" element={<Placeholder name="Dashboard" />} />
        <Route path="/workspaces" element={<Placeholder name="Workspaces" />} />
        <Route path="/workspaces/:wsId" element={<Placeholder name="Collections" />} />
        <Route path="/workspaces/:wsId/collections/:colId" element={<Placeholder name="Documents" />} />
        <Route path="/search" element={<Placeholder name="Search" />} />
        <Route path="/settings" element={<Placeholder name="Settings" />} />
      </Routes>
    </div>
  )
}
