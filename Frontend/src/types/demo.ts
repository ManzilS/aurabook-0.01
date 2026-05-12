export type DemoMode = 'rails' | 'live'
export type ReplaySide = 'left' | 'right'
export type TerminalEntryKind = 'agent' | 'error' | 'note' | 'runtime' | 'success' | 'status'

export interface TerminalEntry {
  id: string
  kind: TerminalEntryKind
  text: string
  timestamp: string
}

export interface ReplayPanelState {
  title: string
  accentClass: string
  tokenCount: number
  statusLabel: string
  complete: boolean
  entries: TerminalEntry[]
}

export interface ReplayEvent {
  id: string
  at: number
  side: ReplaySide
  kind: TerminalEntryKind
  text: string
  tokenCount: number
  statusLabel: string
}

export interface DemoScenario {
  id: string
  label: string
  title: string
  category: 'python' | 'backend' | 'frontend' | 'database'
  prompt: string
  injectedNote: string
  leftSummary: string
  rightSummary: string
  events: ReplayEvent[]
}
