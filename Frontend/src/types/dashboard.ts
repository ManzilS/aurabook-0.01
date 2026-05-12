export type NoteVelocity = 'rising' | 'stable' | 'declining'
export type NoteLanguage = 'Python' | 'JavaScript' | 'TypeScript' | 'Rust'
export type NoteStatus = 'Rising' | 'Stable' | 'Declining'

export interface RatingPoint {
  timestamp: string
  score: number
}

export interface NoteComment {
  id: string
  author: string
  text: string
  createdAt: string
}

export interface NoteEnvironment {
  os: string
  languageVersion: string
  libraries: string[]
}

export interface DashboardAttempt {
  timestamp: string
  model: string | null
  thought: string | null
  tried: string | null
  outcome: string | null
  commands: string[]
  changedFiles: string[]
  tokenCount: number | null
  durationMs: number | null
}

export interface ErrorChainStep {
  id: string
  parentStepId: string | null
  depth: number
  kind: 'root_error' | 'attempt' | 'derived_error' | 'solution'
  title: string
  timestamp: string
  trajectoryId: string | null
  model: string | null
  diagnosis: string | null
  action: string | null
  outcome: string | null
  errorExcerpt: string | null
  errorDetail: string | null
  commands: string[]
  changedFiles: string[]
  tokenCount: number | null
  durationMs: number | null
}

export interface ErrorCluster {
  id: string
  trajectoryId: string | null
  noteId: string | null
  problemTag: string
  summary: string
  diagnosisSummary: string
  fullDiagnosis: string
  rootErrorExcerpt: string
  rootErrorDetail: string
  latestErrorExcerpt: string | null
  unresolved: boolean
  createdAt: string
  updatedAt: string
  language: NoteLanguage
  attemptedModels: string[]
  failedModelCount: number
  attemptCount: number
  failureCount: number
  cascadeErrorCount: number
  relatedTrajectoryIds: string[]
  steps: ErrorChainStep[]
}

export interface KnowledgeNote {
  id: string
  errorClusterId?: string | null
  trajectoryId?: string | null
  problemTag: string
  summary: string
  diagnosisSummary: string
  fullDiagnosis: string
  model: string
  createdAt: string
  velocity: NoteVelocity
  status: NoteStatus
  score: number
  unsolved: boolean
  language: NoteLanguage
  problemDescription: string
  solutionText: string
  environment: NoteEnvironment
  usageCount: number
  attemptedModels: string[]
  unsolvedAttemptCount: number
  failedModelCount: number
  attemptCount: number
  attempts: DashboardAttempt[]
  ratingHistory: RatingPoint[]
  comments: NoteComment[]
  communityContext: string
  recentlyUpdated?: boolean
}

export interface DashboardStats {
  totalNotes: number
  activeAgents: number
  problemsSolvedToday: number
}

export interface DashboardActivity {
  id: string
  text: string
  createdAt: string
  tone: 'success' | 'warning' | 'neutral'
}

export interface DashboardSnapshot {
  notes: KnowledgeNote[]
  errorClusters: ErrorCluster[]
  stats: DashboardStats
  activities: DashboardActivity[]
}

export interface DashboardFilters {
  language: 'All' | NoteLanguage
  status: 'All' | NoteStatus
  model: 'All' | string
  unsolvedOnly: boolean
  view: 'feed' | 'unsolved'
  sortBy:
    | 'newest'
    | 'oldest'
    | 'score'
    | 'usage'
    | 'unsolvedAttempts'
    | 'modelsTried'
    | 'attempts'
  errorSortBy: 'newest' | 'oldest' | 'failures' | 'cascadeErrors' | 'modelsTried' | 'attempts'
}
