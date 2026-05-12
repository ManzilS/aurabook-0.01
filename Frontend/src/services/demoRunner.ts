import { apiClient } from '../lib/api'
import { appConfig } from '../lib/config'
import { demoScenarios } from '../data/scenarios'
import type { DemoScenario, ReplayEvent } from '../types/demo'

function sleep(duration: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, duration))
}

export async function waitBetweenSteps(duration: number): Promise<void> {
  await sleep(duration)
}

function findScenarioByPrompt(prompt: string): DemoScenario {
  const normalized = prompt.toLowerCase()
  if (normalized.includes('pygame')) {
    return demoScenarios[0]
  }
  if (normalized.includes('fastapi') || normalized.includes('sqlalchemy')) {
    return demoScenarios[1]
  }
  if (normalized.includes('next') || normalized.includes('prisma')) {
    return demoScenarios[2]
  }
  if (normalized.includes('database') || normalized.includes('postgres')) {
    return demoScenarios[3]
  }
  return demoScenarios[0]
}

interface LiveCompareEvent {
  at: number
  kind: ReplayEvent['kind']
  text: string
  token_count: number
  status_label: string
}

interface LiveCompareResponse {
  injected_note: string | null
  left_events: LiveCompareEvent[]
  right_events: LiveCompareEvent[]
  baseline_tokens: number
  aurabook_tokens: number
  total_tokens: number
  provider_used: string
  model_used: string
  retrieval_provider_call_count: number
  provider_call_count: number
}

export interface DemoModelsResponse {
  provider: string
  valid: boolean
  models: string[]
  default_model: string
  cache_status: string
  error: string | null
}

export async function fetchWarmModels(provider: string): Promise<DemoModelsResponse> {
  const response = await apiClient.get<DemoModelsResponse>('/v1/demo/models/warm', {
    params: { provider },
    timeout: 10000,
  })
  return response.data
}

export async function validateApiKeyModels(apiKey: string): Promise<DemoModelsResponse> {
  const response = await apiClient.post<DemoModelsResponse>(
    '/v1/demo/models',
    { api_key: apiKey },
    { timeout: 25000 },
  )
  return response.data
}

export function detectProviderFromKey(apiKey: string): string {
  const key = apiKey.trim()
  if (key.startsWith('sk-ant-')) {
    return 'claude'
  }
  if (key.startsWith('AIza')) {
    return 'gemini'
  }
  return 'openai'
}

export async function resolveLiveScenario(apiKey: string, prompt: string, model: string): Promise<DemoScenario> {
  const compareUrl = appConfig.liveCompareUrl
  if (!compareUrl) {
    throw new Error('Live compare endpoint is not configured.')
  }
  if (!apiKey.trim()) {
    throw new Error('Enter an API key to run a real live demo.')
  }

  const response = await apiClient.post<LiveCompareResponse>(
    compareUrl,
    { prompt, api_key: apiKey, model },
    { timeout: 35000 },
  )
  const data = response.data

  const leftEvents: ReplayEvent[] = data.left_events.map((event, index) => ({
    id: `live-left-${index}`,
    at: event.at,
    side: 'left' as const,
    kind: event.kind,
    text: event.text,
    tokenCount: event.token_count,
    statusLabel: event.status_label,
  }))

  const rightEvents: ReplayEvent[] = data.right_events.map((event, index) => ({
    id: `live-right-${index}`,
    at: event.at,
    side: 'right' as const,
    kind: event.kind,
    text: event.text,
    tokenCount: event.token_count,
    statusLabel: event.status_label,
  }))

  const tokenSummary =
    data.total_tokens > 0
      ? `${data.total_tokens.toLocaleString()} completion tokens across ${data.provider_call_count} real ${data.provider_used} requests.`
      : 'Token count unavailable.'
  const retrievalSummary =
    data.retrieval_provider_call_count > 0
      ? ` Includes ${data.retrieval_provider_call_count} retrieval embedding call.`
      : ' Retrieval skipped because this provider has no supported embedding endpoint.'

  return {
    id: `live-${Date.now()}`,
    label: 'Live prompt',
    title: 'Live provider comparison',
    category: findScenarioByPrompt(prompt).category,
    prompt,
    injectedNote: data.injected_note ?? 'No matching AuraBook note was injected.',
    leftSummary: `Baseline ${data.model_used}: ${data.baseline_tokens.toLocaleString()} tokens.`,
    rightSummary: `AuraBook ${data.model_used}: ${data.aurabook_tokens.toLocaleString()} tokens. ${tokenSummary}${retrievalSummary}`,
    events: [...leftEvents, ...rightEvents].sort((a, b) => a.at - b.at),
  }
}
