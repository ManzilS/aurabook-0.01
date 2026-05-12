import { appConfig } from '../lib/config'
import { buildMockSnapshot } from '../data/mockDashboard'
import type { ConnectionStatus } from '../types/app'
import type {
  DashboardActivity,
  DashboardAttempt,
  ErrorCluster,
  ErrorChainStep,
  DashboardSnapshot,
  KnowledgeNote,
} from '../types/dashboard'

interface TransportHandlers {
  onSnapshot: (snapshot: DashboardSnapshot) => void
  onNote: (note: KnowledgeNote) => void
  onActivity: (activity: DashboardActivity) => void
  onStatus: (status: ConnectionStatus) => void
}

interface ApiDashboardSnapshot {
  notes: Array<Record<string, unknown>>
  error_clusters?: Array<Record<string, unknown>>
  stats: Record<string, unknown>
  activities: Array<Record<string, unknown>>
}

function dedupeById<T extends { id: string }>(items: T[]): T[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (!item.id || seen.has(item.id)) {
      return false
    }
    seen.add(item.id)
    return true
  })
}

function bumpNote(note: KnowledgeNote): KnowledgeNote {
  const nextScore = note.score + (note.unsolved ? 0 : 1)
  const nextUsage = note.usageCount + 1

  return {
    ...note,
    score: nextScore,
    usageCount: nextUsage,
    recentlyUpdated: true,
    ratingHistory: [
      ...note.ratingHistory,
      {
        timestamp: new Date().toISOString(),
        score: nextScore,
      },
    ],
  }
}

async function fetchSnapshot(): Promise<DashboardSnapshot> {
  if (!appConfig.dashboardApiUrl) {
    throw new Error('Dashboard API endpoint is not configured.')
  }
  const response = await fetch(`${appConfig.dashboardApiUrl.replace(/\/$/, '')}/snapshot`)
  if (!response.ok) {
    throw new Error(`Failed to load dashboard snapshot: ${response.status}`)
  }
  return normalizeSnapshot((await response.json()) as ApiDashboardSnapshot)
}

function normalizeSnapshot(snapshot: ApiDashboardSnapshot): DashboardSnapshot {
  const notes = dedupeById(
    Array.isArray(snapshot.notes) ? snapshot.notes.map(normalizeNote) : [],
  )
  const activities = dedupeById(
    Array.isArray(snapshot.activities) ? snapshot.activities.map(normalizeActivity) : [],
  )
  return {
    notes,
    errorClusters: dedupeById(
      Array.isArray(snapshot.error_clusters)
        ? snapshot.error_clusters.map(normalizeErrorCluster)
        : [],
    ),
    stats: {
      totalNotes: Number(snapshot.stats?.total_notes ?? 0),
      activeAgents: Number(snapshot.stats?.active_agents ?? 0),
      problemsSolvedToday: Number(snapshot.stats?.problems_solved_today ?? 0),
    },
    activities,
  }
}

function normalizeActivity(activity: Record<string, unknown>): DashboardActivity {
  return {
    id: String(activity.id ?? activity.activity_id ?? `activity-${Date.now()}`),
    text: String(activity.text ?? ''),
    createdAt: String(activity.created_at ?? new Date().toISOString()),
    tone:
      activity.tone === 'success' || activity.tone === 'warning' || activity.tone === 'neutral'
        ? activity.tone
        : 'neutral',
  }
}

function normalizeAttempt(attempt: Record<string, unknown>): DashboardAttempt {
  return {
    timestamp: String(attempt.timestamp ?? new Date().toISOString()),
    model: attempt.model == null ? null : String(attempt.model),
    thought: attempt.thought == null ? null : String(attempt.thought),
    tried: attempt.tried == null ? null : String(attempt.tried),
    outcome: attempt.outcome == null ? null : String(attempt.outcome),
    commands: Array.isArray(attempt.commands) ? attempt.commands.map((item) => String(item)) : [],
    changedFiles: Array.isArray(attempt.changed_files)
      ? attempt.changed_files.map((item) => String(item))
      : Array.isArray(attempt.changedFiles)
        ? attempt.changedFiles.map((item) => String(item))
        : [],
    tokenCount:
      attempt.token_count == null
        ? attempt.tokenCount == null
          ? null
          : Number(attempt.tokenCount)
        : Number(attempt.token_count),
    durationMs:
      attempt.duration_ms == null
        ? attempt.durationMs == null
          ? null
          : Number(attempt.durationMs)
        : Number(attempt.duration_ms),
  }
}

function normalizeErrorStep(step: Record<string, unknown>): ErrorChainStep {
  return {
    id: String(step.id ?? step.step_id ?? ''),
    parentStepId:
      step.parent_step_id == null
        ? step.parentStepId == null
          ? null
          : String(step.parentStepId)
        : String(step.parent_step_id),
    depth: Number(step.depth ?? 0),
    kind:
      step.kind === 'root_error' ||
      step.kind === 'attempt' ||
      step.kind === 'derived_error' ||
      step.kind === 'solution'
        ? step.kind
        : 'attempt',
    title: String(step.title ?? ''),
    timestamp: String(step.timestamp ?? new Date().toISOString()),
    trajectoryId:
      step.trajectory_id == null
        ? step.trajectoryId == null
          ? null
          : String(step.trajectoryId)
        : String(step.trajectory_id),
    model: step.model == null ? null : String(step.model),
    diagnosis: step.diagnosis == null ? null : String(step.diagnosis),
    action: step.action == null ? null : String(step.action),
    outcome: step.outcome == null ? null : String(step.outcome),
    errorExcerpt:
      step.error_excerpt == null
        ? step.errorExcerpt == null
          ? null
          : String(step.errorExcerpt)
        : String(step.error_excerpt),
    errorDetail:
      step.error_detail == null
        ? step.errorDetail == null
          ? null
          : String(step.errorDetail)
        : String(step.error_detail),
    commands: Array.isArray(step.commands) ? step.commands.map((item) => String(item)) : [],
    changedFiles: Array.isArray(step.changed_files)
      ? step.changed_files.map((item) => String(item))
      : Array.isArray(step.changedFiles)
        ? step.changedFiles.map((item) => String(item))
        : [],
    tokenCount:
      step.token_count == null
        ? step.tokenCount == null
          ? null
          : Number(step.tokenCount)
        : Number(step.token_count),
    durationMs:
      step.duration_ms == null
        ? step.durationMs == null
          ? null
          : Number(step.durationMs)
        : Number(step.duration_ms),
  }
}

function normalizeErrorCluster(cluster: Record<string, unknown>): ErrorCluster {
  return {
    id: String(cluster.id ?? cluster.cluster_id ?? ''),
    trajectoryId:
      cluster.trajectory_id == null
        ? cluster.trajectoryId == null
          ? null
          : String(cluster.trajectoryId)
        : String(cluster.trajectory_id),
    noteId:
      cluster.note_id == null
        ? cluster.noteId == null
          ? null
          : String(cluster.noteId)
        : String(cluster.note_id),
    problemTag: String(cluster.problem_tag ?? ''),
    summary: String(cluster.summary ?? ''),
    diagnosisSummary: String(cluster.diagnosis_summary ?? cluster.summary ?? ''),
    fullDiagnosis: String(cluster.full_diagnosis ?? cluster.summary ?? ''),
    rootErrorExcerpt: String(cluster.root_error_excerpt ?? ''),
    rootErrorDetail: String(cluster.root_error_detail ?? ''),
    latestErrorExcerpt:
      cluster.latest_error_excerpt == null
        ? cluster.latestErrorExcerpt == null
          ? null
          : String(cluster.latestErrorExcerpt)
        : String(cluster.latest_error_excerpt),
    unresolved: Boolean(cluster.unresolved ?? false),
    createdAt: String(cluster.created_at ?? new Date().toISOString()),
    updatedAt: String(cluster.updated_at ?? cluster.created_at ?? new Date().toISOString()),
    language:
      cluster.language === 'Python' ||
      cluster.language === 'JavaScript' ||
      cluster.language === 'TypeScript' ||
      cluster.language === 'Rust'
        ? cluster.language
        : 'Python',
    attemptedModels: Array.isArray(cluster.attempted_models)
      ? cluster.attempted_models.map((item) => String(item))
      : Array.isArray(cluster.attemptedModels)
        ? cluster.attemptedModels.map((item) => String(item))
        : [],
    failedModelCount: Number(cluster.failed_model_count ?? cluster.failedModelCount ?? 0),
    attemptCount: Number(cluster.attempt_count ?? cluster.attemptCount ?? 0),
    failureCount: Number(cluster.failure_count ?? cluster.failureCount ?? 0),
    cascadeErrorCount: Number(cluster.cascade_error_count ?? cluster.cascadeErrorCount ?? 0),
    relatedTrajectoryIds: Array.isArray(cluster.related_trajectory_ids)
      ? cluster.related_trajectory_ids.map((item) => String(item))
      : Array.isArray(cluster.relatedTrajectoryIds)
        ? cluster.relatedTrajectoryIds.map((item) => String(item))
        : [],
    steps: Array.isArray(cluster.steps)
      ? cluster.steps.map((step) => normalizeErrorStep(step as Record<string, unknown>))
      : [],
  }
}

function normalizeNote(note: Record<string, unknown>): KnowledgeNote {
  const environment = (note.environment as Record<string, unknown> | undefined) ?? {}
  return {
    id: String(note.id ?? note.note_id ?? ''),
    errorClusterId:
      note.error_cluster_id == null
        ? note.errorClusterId == null
          ? null
          : String(note.errorClusterId)
        : String(note.error_cluster_id),
    trajectoryId: note.trajectory_id == null ? null : String(note.trajectory_id),
    problemTag: String(note.problem_tag ?? ''),
    summary: String(note.summary ?? ''),
    diagnosisSummary: String(note.diagnosis_summary ?? note.summary ?? ''),
    fullDiagnosis: String(note.full_diagnosis ?? note.summary ?? ''),
    model: String(note.model ?? ''),
    createdAt: String(note.created_at ?? new Date().toISOString()),
    velocity:
      note.velocity === 'rising' || note.velocity === 'declining' || note.velocity === 'stable'
        ? note.velocity
        : 'stable',
    status:
      note.status === 'Rising' || note.status === 'Declining' || note.status === 'Stable'
        ? note.status
        : 'Stable',
    score: Number(note.score ?? 0),
    unsolved: Boolean(note.unsolved ?? false),
    language:
      note.language === 'Python' ||
      note.language === 'JavaScript' ||
      note.language === 'TypeScript' ||
      note.language === 'Rust'
        ? note.language
        : 'Python',
    problemDescription: String(note.problem_description ?? ''),
    solutionText: String(note.solution_text ?? ''),
    environment: {
      os: String(environment.os ?? 'Unknown'),
      languageVersion: String(environment.language_version ?? 'Unknown runtime'),
      libraries: Array.isArray(environment.libraries)
        ? environment.libraries.map((item) => String(item))
        : [],
    },
    usageCount: Number(note.usage_count ?? 0),
    attemptedModels: Array.isArray(note.attempted_models)
      ? note.attempted_models.map((item) => String(item))
      : Array.isArray(note.attemptedModels)
        ? note.attemptedModels.map((item) => String(item))
      : [],
    unsolvedAttemptCount: Number(note.unsolved_attempt_count ?? note.unsolvedAttemptCount ?? 0),
    failedModelCount: Number(note.failed_model_count ?? note.failedModelCount ?? 0),
    attemptCount: Number(note.attempt_count ?? note.attemptCount ?? 0),
    attempts: Array.isArray(note.attempts)
      ? note.attempts.map((attempt) => normalizeAttempt(attempt as Record<string, unknown>))
      : [],
    ratingHistory: Array.isArray(note.rating_history)
      ? note.rating_history.map((point) => {
          const ratingPoint = point as Record<string, unknown>
          return {
            timestamp: String(ratingPoint.timestamp ?? new Date().toISOString()),
            score: Number(ratingPoint.score ?? 0),
          }
        })
      : [],
    comments: Array.isArray(note.comments)
      ? note.comments.map((comment) => {
          const entry = comment as Record<string, unknown>
          return {
            id: String(entry.id ?? ''),
            author: String(entry.author ?? 'community'),
            text: String(entry.text ?? ''),
            createdAt: String(entry.created_at ?? new Date().toISOString()),
          }
        })
      : [],
    communityContext: String(note.community_context ?? ''),
  }
}

function startPreviewPolling(handlers: TransportHandlers): () => void {
  let active = true
  let noteCursor = 0
  const seed = buildMockSnapshot()

  handlers.onStatus('preview')
  handlers.onSnapshot(seed)

  const interval = window.setInterval(() => {
    if (!active) {
      return
    }

    const note = bumpNote(seed.notes[noteCursor % seed.notes.length])
    noteCursor += 1
    seed.notes = [note, ...seed.notes.filter((candidate) => candidate.id !== note.id)]
    handlers.onNote(note)
    handlers.onActivity({
      id: `activity-${Date.now()}`,
      text: note.unsolved
        ? `New unsolved cluster detected — ${note.problemTag}`
        : `Agent solved: ${note.problemTag}`,
      createdAt: new Date().toISOString(),
      tone: note.unsolved ? 'warning' : 'success',
    })
  }, 7000)

  return () => {
    active = false
    window.clearInterval(interval)
  }
}

function startLivePolling(handlers: TransportHandlers): () => void {
  let active = true

  handlers.onStatus('polling')

  const interval = window.setInterval(async () => {
    if (!active) {
      return
    }

    try {
      const snapshot = await fetchSnapshot()
      handlers.onSnapshot(snapshot)
      handlers.onStatus('polling')
    } catch {
      handlers.onStatus('disconnected')
    }
  }, 8000)

  return () => {
    active = false
    window.clearInterval(interval)
  }
}

export async function bootstrapDashboardTransport(
  handlers: TransportHandlers,
): Promise<() => void> {
  let initialSnapshot: DashboardSnapshot | null = null

  try {
    handlers.onStatus('connecting')
    initialSnapshot = await fetchSnapshot()
    handlers.onSnapshot(initialSnapshot)
  } catch {
    return startPreviewPolling(handlers)
  }

  if (!appConfig.websocketUrl) {
    return startLivePolling(handlers)
  }

  let attempts = 0
  let closed = false
  let socket: WebSocket | null = null
  let pollCleanup: (() => void) | null = null

  const connect = (): void => {
    if (closed) {
      return
    }

    handlers.onStatus('connecting')
    socket = new WebSocket(appConfig.websocketUrl)

    socket.onopen = () => {
      attempts = 0
      handlers.onStatus('connected')
    }

    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as
        | { type: 'snapshot'; snapshot: ApiDashboardSnapshot }
        | { type: 'note'; note: Record<string, unknown> }
        | { type: 'activity'; activity: Record<string, unknown> }

      if (payload.type === 'snapshot') {
        handlers.onSnapshot(normalizeSnapshot(payload.snapshot))
      }

      if (payload.type === 'note') {
        handlers.onNote({ ...normalizeNote(payload.note), recentlyUpdated: true })
      }

      if (payload.type === 'activity') {
        handlers.onActivity(normalizeActivity(payload.activity))
      }
    }

    socket.onerror = () => {
      socket?.close()
    }

    socket.onclose = () => {
      if (closed) {
        return
      }

      attempts += 1
      if (attempts >= 2) {
        pollCleanup = startLivePolling(handlers)
        return
      }

      window.setTimeout(connect, 1200 * attempts)
    }
  }

  connect()

  return () => {
    closed = true
    socket?.close()
    pollCleanup?.()
    handlers.onStatus('standby')
  }
}
