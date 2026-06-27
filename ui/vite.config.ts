import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Where the dev server proxies /api and /mcp. Defaults to the host-exposed API
// port (running `npm run dev` on the host); the Docker dev overlay sets it to the
// in-network api service (VITE_API_PROXY=http://api:8000).
const apiTarget = process.env.VITE_API_PROXY || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // listen on 0.0.0.0 so the dev server is reachable from the container/LAN
    port: 3000,
    // Bind-mounted source from a Windows/macOS host emits no inotify events the
    // Linux container can see, so the dev overlay sets VITE_USE_POLLING=true to make
    // the watcher poll — otherwise hot-reload silently never fires. Off by default
    // so native `npm run dev` uses efficient native fs events.
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      '/api': { target: apiTarget, rewrite: (path) => path.replace(/^\/api/, '') },
      '/mcp': { target: apiTarget },
      // Realtime WebSocket bridge (ingestion progress). ws:true performs the HTTP
      // 101 upgrade; no rewrite — the API serves the socket at /ws.
      '/ws': { target: apiTarget, ws: true },
    },
  },
})
