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
    proxy: {
      '/api': { target: apiTarget, rewrite: (path) => path.replace(/^\/api/, '') },
      '/mcp': { target: apiTarget },
    },
  },
})
