function envOrDefault(value: string | undefined, fallback: string): string {
  const normalized = value?.trim()
  return normalized ? normalized : fallback
}

const apiBaseUrl = envOrDefault(
  import.meta.env.VITE_AURABOOK_API_BASE_URL,
  import.meta.env.DEV ? 'http://127.0.0.1:8000' : 'https://aurabook-api.manzilsapkota.workers.dev',
).replace(/\/$/, '')

function deriveDashboardApiUrl(): string {
  if (!apiBaseUrl) {
    return ''
  }
  return `${apiBaseUrl}/v1/dashboard`
}

function deriveWebsocketUrl(): string {
  if (!apiBaseUrl) {
    return ''
  }
  const wsBase = apiBaseUrl.replace(/^http/i, 'ws')
  return `${wsBase}/v1/dashboard/ws`
}

export const appConfig = {
  githubUrl: envOrDefault(import.meta.env.VITE_AURABOOK_GITHUB_URL, 'https://github.com/ManzilS'),
  linkedinUrl: envOrDefault(import.meta.env.VITE_AURABOOK_LINKEDIN_URL, 'https://www.linkedin.com/in/manzilsapkota'),
  apiBaseUrl,
  dashboardApiUrl: envOrDefault(import.meta.env.VITE_AURABOOK_DASHBOARD_API_URL, deriveDashboardApiUrl()),
  websocketUrl: envOrDefault(import.meta.env.VITE_AURABOOK_WS_URL, deriveWebsocketUrl()),
  liveCompareUrl: envOrDefault(
    import.meta.env.VITE_AURABOOK_LIVE_COMPARE_URL,
    apiBaseUrl ? `${apiBaseUrl}/v1/demo/compare` : '',
  ),
} as const
