import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBase = (env.VITE_AURABOOK_API_BASE_URL || (mode === 'development'
    ? 'http://127.0.0.1:8000'
    : 'https://aurabook-api.manzilsapkota.workers.dev')).replace(/\/$/, '')
  const htmlSafeApiBase = apiBase.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  return {
  plugins: [react(), {
    name: 'static-api-links',
    transformIndexHtml(html) {
      return html.replaceAll('https://aurabook-api.manzilsapkota.workers.dev', htmlSafeApiBase)
    },
  }],
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
  }
})
