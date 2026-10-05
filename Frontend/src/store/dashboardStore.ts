import { create } from 'zustand'
import { bootstrapDashboardTransport } from '../services/dashboardTransport'
import { downloadTextFile } from '../lib/download'
import { useAppStore } from './appStore'
import type { ConnectionStatus } from '../types/app'
import type {
  DashboardActivity,
  ErrorCluster,
  DashboardFilters,
  DashboardSnapshot,
  DashboardStats,
  KnowledgeNote,
} from '../types/dashboard'

let disconnectTransport: null | (() => void) = null
let transportGeneration = 0

const defaultFilters: DashboardFilters = {
  language: 'All',
  status: 'All',
  model: 'All',
  unsolvedOnly: false,
  view: 'feed',
  sortBy: 'newest',
  errorSortBy: 'newest',
}

function computeStats(notes: KnowledgeNote[]): DashboardStats {
  return {
    totalNotes: notes.length,
    activeAgents: 0,
    problemsSolvedToday: notes.filter((note) => !note.unsolved && note.createdAt.slice(0, 10) === new Date().toISOString().slice(0, 10)).length,
  }
}

function upsertNote(notes: KnowledgeNote[], incoming: KnowledgeNote): KnowledgeNote[] {
  const remaining = notes.filter((note) => note.id !== incoming.id)
  return [{ ...incoming, recentlyUpdated: true }, ...remaining]
}

function sortActivities(activities: DashboardActivity[]): DashboardActivity[] {
  return [...activities].sort((first, second) => second.createdAt.localeCompare(first.createdAt))
}

function uniqueNotes(notes: KnowledgeNote[]): KnowledgeNote[] {
  const seen = new Set<string>()
  return notes.filter((note) => {
    if (!note.id || seen.has(note.id)) {
      return false
    }
    seen.add(note.id)
    return true
  })
}

function markdownForNote(note: KnowledgeNote): string {
  const attemptSection = note.attempts.length
    ? [
        '',
        '## Attempt History',
        ...note.attempts.map((attempt, index) =>
          [
            `### Attempt ${index + 1}`,
            `- Time: ${attempt.timestamp}`,
            `- Model: ${attempt.model ?? 'Unknown'}`,
            `- Thought: ${attempt.thought ?? 'Not captured'}`,
            `- Tried: ${attempt.tried ?? 'Not captured'}`,
            `- Outcome: ${attempt.outcome ?? 'Unknown'}`,
            `- Commands: ${attempt.commands.join(' | ') || 'None recorded'}`,
            `- Changed files: ${attempt.changedFiles.join(', ') || 'None recorded'}`,
            `- Tokens: ${attempt.tokenCount ?? 'Unknown'}`,
            `- Duration ms: ${attempt.durationMs ?? 'Unknown'}`,
            '',
          ].join('\n'),
        ),
      ].join('\n')
    : ''

  return [
    `# ${note.problemTag}`,
    '',
    `Model: ${note.model}`,
    `Created: ${note.createdAt}`,
    `Score: ${note.score}`,
    `Diagnosis summary: ${note.diagnosisSummary}`,
    `Models tried: ${note.attemptedModels.join(', ') || 'Unknown'}`,
    `Failed sessions: ${note.unsolvedAttemptCount}`,
    `Distinct failed models: ${note.failedModelCount}`,
    `Captured attempts: ${note.attemptCount}`,
    '',
    '## Problem',
    note.problemDescription,
    '',
    '## Diagnosis',
    note.fullDiagnosis,
    '',
    '## Solution',
    note.solutionText || 'No community solution captured yet.',
    '',
    '## Environment',
    `- OS: ${note.environment.os}`,
    `- Language: ${note.environment.languageVersion}`,
    `- Libraries: ${note.environment.libraries.join(', ')}`,
    attemptSection,
  ]
    .filter(Boolean)
    .join('\n')
}

function markdownForErrorCluster(cluster: ErrorCluster): string {
  const stepSection = cluster.steps.length
    ? [
        '',
        '## Error Chain',
        ...cluster.steps.map((step) =>
          [
            `### ${step.title}`,
            `- Kind: ${step.kind}`,
            `- Time: ${step.timestamp}`,
            `- Model: ${step.model ?? 'Unknown'}`,
            `- Diagnosis: ${step.diagnosis ?? 'Not captured'}`,
            `- Action: ${step.action ?? 'Not captured'}`,
            `- Outcome: ${step.outcome ?? 'Unknown'}`,
            `- Returned error: ${step.errorExcerpt ?? 'None'}`,
            `- Commands: ${step.commands.join(' | ') || 'None recorded'}`,
            `- Changed files: ${step.changedFiles.join(', ') || 'None recorded'}`,
            '',
          ].join('\n'),
        ),
      ].join('\n')
    : ''

  return [
    `# ${cluster.problemTag}`,
    '',
    `Unresolved: ${cluster.unresolved ? 'yes' : 'no'}`,
    `Created: ${cluster.createdAt}`,
    `Updated: ${cluster.updatedAt}`,
    `Models tried: ${cluster.attemptedModels.join(', ') || 'Unknown'}`,
    `Failure count: ${cluster.failureCount}`,
    `Cascade errors: ${cluster.cascadeErrorCount}`,
    '',
    '## Root Error',
    cluster.rootErrorDetail,
    '',
    '## Diagnosis',
    cluster.fullDiagnosis,
    stepSection,
  ]
    .filter(Boolean)
    .join('\n')
}

interface DashboardState {
  loading: boolean
  initialized: boolean
  connectionStatus: ConnectionStatus
  notes: KnowledgeNote[]
  errorClusters: ErrorCluster[]
  activities: DashboardActivity[]
  stats: DashboardStats
  filters: DashboardFilters
  selectedNoteId: string | null
  selectedErrorClusterId: string | null
  error: string | null
  initialize: () => Promise<void>
  teardown: () => void
  applySnapshot: (snapshot: DashboardSnapshot) => void
  setFilter: <K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) => void
  selectNote: (noteId: string) => void
  selectErrorCluster: (clusterId: string | null) => void
  vote: (noteId: string, direction: 'up' | 'down') => void
  addComment: (noteId: string, text: string) => void
  reportOutdated: (noteId: string) => void
  downloadNote: (noteId: string) => void
  downloadErrorCluster: (clusterId: string) => void
  submitSolution: (noteId: string, solutionText: string) => void
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  loading: true,
  initialized: false,
  connectionStatus: 'standby',
  notes: [],
  errorClusters: [],
  activities: [],
  stats: {
    totalNotes: 0,
    activeAgents: 0,
    problemsSolvedToday: 0,
  },
  filters: defaultFilters,
  selectedNoteId: null,
  selectedErrorClusterId: null,
  error: null,
  initialize: async () => {
    if (get().initialized) {
      return
    }

    const generation = ++transportGeneration
    set({ loading: true, initialized: true, error: null })
    const disconnect = await bootstrapDashboardTransport({
      onSnapshot: (snapshot) => {
        if (generation !== transportGeneration) return
        const nextNotes = uniqueNotes(snapshot.notes)
        set({
          loading: false,
          initialized: true,
          notes: nextNotes,
          errorClusters: snapshot.errorClusters,
          activities: sortActivities(snapshot.activities),
          stats: snapshot.stats,
          selectedNoteId: get().selectedNoteId ?? nextNotes[0]?.id ?? null,
          selectedErrorClusterId: get().selectedErrorClusterId ?? snapshot.errorClusters[0]?.id ?? null,
        })
      },
      onNote: (note) => {
        if (generation !== transportGeneration) return
        const nextNotes = upsertNote(get().notes, note)
        set({
          notes: nextNotes,
          stats: { ...get().stats, totalNotes: nextNotes.length },
          selectedNoteId: get().selectedNoteId ?? note.id,
        })
      },
      onActivity: (activity) => {
        if (generation !== transportGeneration) return
        set({
          activities: sortActivities([activity, ...get().activities]).slice(0, 12),
        })
      },
      onStatus: (status) => {
        if (generation !== transportGeneration) return
        useAppStore.getState().setConnectionStatus(status)
        set({
          connectionStatus: status,
          error: status === 'disconnected' ? 'Cannot reach the dashboard API. Retrying automatically.' : null,
        })
      },
    })
    if (generation !== transportGeneration) disconnect()
    else disconnectTransport = disconnect
  },
  teardown: () => {
    ++transportGeneration
    disconnectTransport?.()
    disconnectTransport = null
    useAppStore.getState().setConnectionStatus('standby')
    set({ connectionStatus: 'standby', initialized: false })
  },
  applySnapshot: (snapshot) => {
    const nextNotes = uniqueNotes(snapshot.notes)
    set({
      notes: nextNotes,
      activities: snapshot.activities,
      errorClusters: snapshot.errorClusters,
      stats: snapshot.stats,
      selectedNoteId: get().selectedNoteId ?? nextNotes[0]?.id ?? null,
      selectedErrorClusterId: get().selectedErrorClusterId ?? snapshot.errorClusters[0]?.id ?? null,
    })
  },
  setFilter: (key, value) =>
    set({
      filters: {
        ...get().filters,
        [key]: value,
      },
    }),
  selectNote: (noteId) => set({ selectedNoteId: noteId }),
  selectErrorCluster: (clusterId) => set({ selectedErrorClusterId: clusterId }),
  vote: (noteId, direction) => {
    const notes = get().notes.map((note) => {
      if (note.id !== noteId) {
        return note
      }
      const delta = direction === 'up' ? 1 : -1
      return {
        ...note,
        score: note.score + delta,
        ratingHistory: [
          ...note.ratingHistory,
          { timestamp: new Date().toISOString(), score: note.score + delta },
        ],
      }
    })

    set({
      notes,
      activities: sortActivities([
        {
          id: `vote-${Date.now()}`,
          text: `Local rating preview: ${direction} — ${notes.find((note) => note.id === noteId)?.problemTag ?? 'AuraBook note'}`,
          createdAt: new Date().toISOString(),
          tone: direction === 'up' ? 'success' : 'warning',
        },
        ...get().activities,
      ]).slice(0, 12),
    })
  },
  addComment: (noteId, text) => {
    const trimmed = text.trim()
    if (!trimmed) {
      return
    }

    set({
      notes: get().notes.map((note) =>
        note.id === noteId
          ? {
              ...note,
              comments: [
                ...note.comments,
                {
                  id: `comment-${Date.now()}`,
                  author: 'developer',
                  text: trimmed,
                  createdAt: new Date().toISOString(),
                },
              ],
            }
          : note,
      ),
    })
  },
  reportOutdated: (noteId) => {
    set({
      notes: get().notes.map((note) =>
        note.id === noteId
          ? {
              ...note,
              velocity: 'declining',
              status: 'Declining',
            }
          : note,
      ),
      activities: sortActivities([
        {
          id: `report-${Date.now()}`,
          text: `Local outdated flag: ${get().notes.find((note) => note.id === noteId)?.problemTag ?? 'AuraBook note'}`,
          createdAt: new Date().toISOString(),
          tone: 'warning',
        },
        ...get().activities,
      ]).slice(0, 12),
    })
  },
  downloadNote: (noteId) => {
    const note = get().notes.find((candidate) => candidate.id === noteId)
    if (!note) {
      return
    }
    downloadTextFile(`${note.problemTag.toLowerCase().replace(/\s+/g, '-')}.md`, markdownForNote(note))
  },
  downloadErrorCluster: (clusterId) => {
    const cluster = get().errorClusters.find((candidate) => candidate.id === clusterId)
    if (!cluster) {
      return
    }
    downloadTextFile(
      `${cluster.problemTag.toLowerCase().replace(/\s+/g, '-')}-errors.md`,
      markdownForErrorCluster(cluster),
    )
  },
  submitSolution: (noteId, solutionText) => {
    const trimmed = solutionText.trim()
    if (!trimmed) {
      return
    }

    const nextNotes = get().notes.map((note) =>
      note.id === noteId
        ? {
            ...note,
            solutionText: trimmed,
            unsolved: false,
            velocity: 'rising' as const,
            status: 'Rising' as const,
            score: note.score + 2,
            ratingHistory: [
              ...note.ratingHistory,
              { timestamp: new Date().toISOString(), score: note.score + 2 },
            ],
          }
        : note,
    )

    set({
      notes: nextNotes,
      stats: computeStats(nextNotes),
      activities: sortActivities([
        {
          id: `solve-${Date.now()}`,
          text: `Local solution draft: ${nextNotes.find((note) => note.id === noteId)?.problemTag ?? 'AuraBook note'}`,
          createdAt: new Date().toISOString(),
          tone: 'success',
        },
        ...get().activities,
      ]).slice(0, 12),
    })
  },
}))
