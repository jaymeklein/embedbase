const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  // Workspaces
  listWorkspaces: () => request<any[]>('/workspaces'),
  createWorkspace: (body: object) => request('/workspaces', { method: 'POST', body: JSON.stringify(body) }),
  getWorkspace: (id: string) => request<any>(`/workspaces/${id}`),
  updateWorkspace: (id: string, body: object) => request(`/workspaces/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteWorkspace: (id: string) => request(`/workspaces/${id}`, { method: 'DELETE' }),

  // Collections
  listCollections: (wsId: string) => request<any[]>(`/workspaces/${wsId}/collections`),
  createCollection: (wsId: string, body: object) => request(`/workspaces/${wsId}/collections`, { method: 'POST', body: JSON.stringify(body) }),
  deleteCollection: (wsId: string, colId: string) => request(`/workspaces/${wsId}/collections/${colId}`, { method: 'DELETE' }),

  // API keys
  createApiKey: (wsId: string, colId: string, body: object) =>
    request(`/workspaces/${wsId}/collections/${colId}/keys`, { method: 'POST', body: JSON.stringify(body) }),
  listApiKeys: (wsId: string, colId: string) => request<any[]>(`/workspaces/${wsId}/collections/${colId}/keys`),
  revokeApiKey: (wsId: string, colId: string, keyId: string) =>
    request(`/workspaces/${wsId}/collections/${colId}/keys/${keyId}`, { method: 'DELETE' }),

  // Search
  search: (body: object) => request<any>('/search', { method: 'POST', body: JSON.stringify(body) }),

  // Health
  healthz: () => request<any>('/healthz'),
}
