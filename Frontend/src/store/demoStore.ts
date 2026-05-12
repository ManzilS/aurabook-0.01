import { startTransition } from 'react'
import { create } from 'zustand'
import { demoScenarios, livePromptSuggestions } from '../data/scenarios'
import {
  detectProviderFromKey,
  fetchWarmModels,
  resolveLiveScenario,
  validateApiKeyModels,
  waitBetweenSteps,
} from '../services/demoRunner'
import { extractPythonSnippets, runPythonSnippet } from '../services/pyodideRunner'
import type { DemoMode, DemoScenario, ReplayPanelState, ReplaySide, TerminalEntry } from '../types/demo'

let replayRunId = 0
let keyValidationRunId = 0
let keyValidationTimer: number | undefined
const API_KEY_STORAGE_KEY = 'aurabook.liveDemo.apiKey'
const API_KEY_REMEMBER_STORAGE_KEY = 'aurabook.liveDemo.rememberApiKey'
const MODEL_STORAGE_KEY = 'aurabook.liveDemo.model'

function getLocalStorage(): Storage | null {
  return typeof window === 'undefined' ? null : window.localStorage
}

function getSessionStorage(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function loadRememberApiKey(): boolean {
  return getLocalStorage()?.getItem(API_KEY_REMEMBER_STORAGE_KEY) === 'true'
}

function loadStoredApiKey(): string {
  const localStorage = getLocalStorage()
  const sessionStorage = getSessionStorage()
  if (loadRememberApiKey() && localStorage) {
    return localStorage.getItem(API_KEY_STORAGE_KEY) ?? ''
  }
  if (sessionStorage) {
    return sessionStorage.getItem(API_KEY_STORAGE_KEY) ?? ''
  }
  return ''
}

function loadStoredModel(): string {
  return getLocalStorage()?.getItem(MODEL_STORAGE_KEY) ?? ''
}

function storeApiKey(value: string, remember: boolean): void {
  const localStorage = getLocalStorage()
  const sessionStorage = getSessionStorage()
  if (!localStorage || !sessionStorage) {
    return
  }
  if (remember) {
    localStorage.setItem(API_KEY_REMEMBER_STORAGE_KEY, 'true')
    localStorage.setItem(API_KEY_STORAGE_KEY, value)
    sessionStorage.removeItem(API_KEY_STORAGE_KEY)
    return
  }
  localStorage.removeItem(API_KEY_REMEMBER_STORAGE_KEY)
  localStorage.removeItem(API_KEY_STORAGE_KEY)
  if (value) {
    sessionStorage.setItem(API_KEY_STORAGE_KEY, value)
  } else {
    sessionStorage.removeItem(API_KEY_STORAGE_KEY)
  }
}

function buildPanel(title: string, accentClass: string): ReplayPanelState {
  return {
    title,
    accentClass,
    tokenCount: 0,
    statusLabel: 'Waiting for run',
    complete: false,
    entries: [],
  }
}

function buildTerminalEntry(text: string, kind: TerminalEntry['kind']): TerminalEntry {
  return {
    id: `${kind}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    kind,
    text,
    timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }
}

function appendPanelEntry(
  set: (partial: Partial<DemoState> | ((state: DemoState) => Partial<DemoState>)) => void,
  side: ReplaySide,
  entry: TerminalEntry,
  statusLabel: string,
  tokenCount?: number,
): void {
  startTransition(() => {
    set((state) => {
      const target = side === 'left' ? state.left : state.right
      const nextTarget: ReplayPanelState = {
        ...target,
        tokenCount: tokenCount ?? target.tokenCount,
        statusLabel,
        complete: false,
        entries: [...target.entries, entry],
      }

      return side === 'left' ? { left: nextTarget } : { right: nextTarget }
    })
  })
}

function initialPanels(mode: DemoMode = 'rails'): { left: ReplayPanelState; right: ReplayPanelState } {
  return {
    left: buildPanel(
      mode === 'live' ? 'Live Baseline — No AuraBook' : 'Codex Agent — No Memory',
      'from-warning/30 to-danger/20',
    ),
    right: buildPanel(
      mode === 'live' ? 'Live Agent + AuraBook' : 'Codex Agent + AuraBook',
      'from-success/30 to-accent/20',
    ),
  }
}

interface DemoState {
  mode: DemoMode
  selectedScenarioId: string
  selectedPrompt: string
  apiKey: string
  rememberApiKey: boolean
  apiKeyStatus: 'idle' | 'checking' | 'valid' | 'invalid'
  apiKeyStatusMessage: string
  provider: string
  availableModels: string[]
  selectedModel: string
  running: boolean
  error: string | null
  mobileVisiblePanel: ReplaySide
  activeScenario: DemoScenario
  left: ReplayPanelState
  right: ReplayPanelState
  setMode: (mode: DemoMode) => void
  setScenarioId: (scenarioId: string) => void
  setSelectedPrompt: (prompt: string) => void
  setApiKey: (value: string) => void
  setRememberApiKey: (value: boolean) => void
  setSelectedModel: (value: string) => void
  refreshModelList: () => Promise<void>
  setMobileVisiblePanel: (panel: ReplaySide) => void
  runSelectedScenario: () => Promise<void>
  runLiveScenario: () => Promise<void>
  resetPanels: () => void
}

async function executeScenario(
  scenario: DemoScenario,
  set: (partial: Partial<DemoState> | ((state: DemoState) => Partial<DemoState>)) => void,
  mode: DemoMode = 'rails',
): Promise<void> {
  replayRunId += 1
  const currentRunId = replayRunId
  const reset = initialPanels(mode)

  set({
    running: true,
    error: null,
    activeScenario: scenario,
    left: reset.left,
    right: reset.right,
  })

  let elapsed = 0
  for (const event of scenario.events) {
    if (replayRunId !== currentRunId) {
      return
    }

    await waitBetweenSteps(event.at - elapsed)
    elapsed = event.at

    startTransition(() => {
      set((state) => {
        const target = event.side === 'left' ? state.left : state.right
        const nextTarget: ReplayPanelState = {
          ...target,
          tokenCount: event.tokenCount,
          statusLabel: event.statusLabel,
          complete: event.kind === 'success' && mode !== 'live',
          entries: [...target.entries, buildTerminalEntry(event.text, event.kind)],
        }

        return event.side === 'left' ? { left: nextTarget } : { right: nextTarget }
      })
    })

    if (mode === 'live' && event.kind === 'success') {
      await executePythonFromProviderResponse(event, set, currentRunId)
    }
  }

  if (replayRunId === currentRunId) {
    set({ running: false })
  }
}

async function executePythonFromProviderResponse(
  event: DemoScenario['events'][number],
  set: (partial: Partial<DemoState> | ((state: DemoState) => Partial<DemoState>)) => void,
  currentRunId: number,
): Promise<void> {
  const snippets = extractPythonSnippets(event.text)
  if (snippets.length === 0) {
    appendPanelEntry(
      set,
      event.side,
      buildTerminalEntry('No Python code block was found in this provider response, so Pyodide did not run.', 'status'),
      'No Python to run',
      event.tokenCount,
    )
    return
  }

  for (const [index, snippet] of snippets.entries()) {
    if (replayRunId !== currentRunId) {
      return
    }

    appendPanelEntry(
      set,
      event.side,
      buildTerminalEntry(`Running Python snippet ${index + 1} of ${snippets.length} in Pyodide...\n\n${snippet}`, 'status'),
      'Running Python',
      event.tokenCount,
    )

    try {
      const result = await runPythonSnippet(snippet)
      if (replayRunId !== currentRunId) {
        return
      }

      const outputParts = [
        result.stdout ? `stdout:\n${result.stdout.trimEnd()}` : '',
        result.stderr ? `stderr:\n${result.stderr.trimEnd()}` : '',
        result.error ? `error:\n${result.error.trimEnd()}` : '',
      ].filter(Boolean)

      appendPanelEntry(
        set,
        event.side,
        buildTerminalEntry(outputParts.join('\n\n') || 'Python completed with no stdout/stderr.', result.error ? 'error' : 'runtime'),
        result.error ? 'Python error' : 'Python complete',
        event.tokenCount,
      )
    } catch (error) {
      if (replayRunId !== currentRunId) {
        return
      }
      appendPanelEntry(
        set,
        event.side,
        buildTerminalEntry(
          error instanceof Error ? `Pyodide could not run this code:\n${error.message}` : 'Pyodide could not run this code.',
          'error',
        ),
        'Pyodide error',
        event.tokenCount,
      )
    }
  }
}

export const useDemoStore = create<DemoState>((set, get) => ({
  mode: 'rails',
  selectedScenarioId: demoScenarios[0].id,
  selectedPrompt: livePromptSuggestions[0],
  apiKey: loadStoredApiKey(),
  rememberApiKey: loadRememberApiKey(),
  apiKeyStatus: 'idle',
  apiKeyStatusMessage: 'Enter an API key to load models.',
  provider: 'openai',
  availableModels: [],
  selectedModel: loadStoredModel(),
  running: false,
  error: null,
  mobileVisiblePanel: 'right',
  activeScenario: demoScenarios[0],
  ...initialPanels(),
  setMode: (mode) => set({ mode }),
  setScenarioId: (scenarioId) => {
    const next = demoScenarios.find((scenario) => scenario.id === scenarioId) ?? demoScenarios[0]
    set((state) => ({
      selectedScenarioId: next.id,
      selectedPrompt: next.prompt,
      activeScenario: next,
      ...initialPanels(state.mode),
    }))
  },
  setSelectedPrompt: (prompt) => set({ selectedPrompt: prompt }),
  setApiKey: (value) => {
    storeApiKey(value, get().rememberApiKey)
    const provider = detectProviderFromKey(value)
    set({
      apiKey: value,
      provider,
      apiKeyStatus: value.trim() ? 'checking' : 'idle',
      apiKeyStatusMessage: value.trim() ? 'Checking API key...' : 'Enter an API key to load models.',
    })
    if (keyValidationTimer !== undefined) {
      window.clearTimeout(keyValidationTimer)
    }
    keyValidationTimer = window.setTimeout(() => {
      void get().refreshModelList()
    }, value.trim().length > 16 ? 500 : 150)
  },
  setRememberApiKey: (value) => {
    storeApiKey(get().apiKey, value)
    set({ rememberApiKey: value })
  },
  setSelectedModel: (value) => {
    getLocalStorage()?.setItem(MODEL_STORAGE_KEY, value)
    set({ selectedModel: value })
  },
  refreshModelList: async () => {
    const runId = ++keyValidationRunId
    const apiKey = get().apiKey.trim()
    const provider = detectProviderFromKey(apiKey)
    if (!apiKey) {
      set({
        apiKeyStatus: 'idle',
        apiKeyStatusMessage: 'Enter an API key to load models.',
        availableModels: [],
      })
      return
    }

    try {
      if (apiKey.length < 16) {
        const warm = await fetchWarmModels(provider)
        if (runId !== keyValidationRunId) {
          return
        }
        set({
          provider: warm.provider,
          availableModels: warm.models,
          selectedModel: get().selectedModel || warm.default_model,
          apiKeyStatus: 'checking',
          apiKeyStatusMessage: 'Waiting for the full key to validate access.',
        })
        return
      }

      const result = await validateApiKeyModels(apiKey)
      if (runId !== keyValidationRunId) {
        return
      }
      const selectedModel =
        result.models.includes(get().selectedModel) ? get().selectedModel : result.default_model
      if (selectedModel) {
        getLocalStorage()?.setItem(MODEL_STORAGE_KEY, selectedModel)
      }
      set({
        provider: result.provider,
        availableModels: result.models,
        selectedModel,
        apiKeyStatus: result.valid ? 'valid' : 'invalid',
        apiKeyStatusMessage: result.valid
          ? `API key works. Models loaded from ${result.cache_status}.`
          : result.error ?? 'API key could not be validated.',
      })
    } catch (error) {
      if (runId !== keyValidationRunId) {
        return
      }
      set({
        apiKeyStatus: 'invalid',
        apiKeyStatusMessage: error instanceof Error ? error.message : 'API key validation failed.',
      })
    }
  },
  setMobileVisiblePanel: (panel) => set({ mobileVisiblePanel: panel }),
  runSelectedScenario: async () => {
    await executeScenario(get().activeScenario, set)
  },
  runLiveScenario: async () => {
    const reset = initialPanels('live')
    set({
      running: true,
      error: null,
      left: reset.left,
      right: {
        ...reset.right,
        statusLabel: 'Preparing live call',
        entries: [buildTerminalEntry('Preparing live AuraBook comparison...', 'agent')],
      },
    })

    try {
      const scenario = await resolveLiveScenario(get().apiKey, get().selectedPrompt, get().selectedModel)
      await executeScenario(scenario, set, 'live')
    } catch (error) {
      set({
        running: false,
        error:
          error instanceof Error
            ? error.message
            : 'Live demo could not start. Falling back to the guided replay.',
      })
    }
  },
  resetPanels: () => {
    replayRunId += 1
    set((state) => ({
      running: false,
      error: null,
      ...initialPanels(state.mode),
    }))
  },
}))
