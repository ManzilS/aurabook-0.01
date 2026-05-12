import { ArrowUpRight, Route, Sparkles } from 'lucide-react'
import { Button } from '../common/Button'
import { formatCompactNumber, formatRelativeTime } from '../../lib/formatters'
import type { ErrorCluster, KnowledgeNote } from '../../types/dashboard'

interface DashboardDetailPanelProps {
  note: KnowledgeNote | null
  errorCluster?: ErrorCluster | null
  onOpenNotePage?: () => void
  onOpenErrorPage?: () => void
}

function clipText(value: string | null | undefined, maxLength: number): string {
  if (!value) {
    return 'No additional detail captured yet.'
  }
  if (value.length <= maxLength) {
    return value
  }
  return `${value.slice(0, maxLength).trimEnd()}…`
}

export function DashboardDetailPanel({
  note,
  errorCluster,
  onOpenNotePage,
  onOpenErrorPage,
}: DashboardDetailPanelProps) {
  if (!note) {
    return (
      <div className="surface-glass panel-border flex min-h-[28rem] items-center justify-center rounded-[2rem] p-8 text-center text-text-secondary">
        Select a note to inspect the quick diagnosis and the latest repair attempts.
      </div>
    )
  }

  const recentAttempts = [...note.attempts].slice(-3).reverse()
  const diagnosisPreview = clipText(note.diagnosisSummary || note.fullDiagnosis, 220)
  const solutionPreview = note.unsolved
    ? clipText(errorCluster?.latestErrorExcerpt || note.summary, 220)
    : clipText(note.solutionText || note.summary, 220)

  return (
    <div className="surface-glass panel-border flex min-w-0 flex-col gap-5 rounded-[2rem] p-5">
      <div className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-xs uppercase tracking-[0.35em] text-accent">Selected note</p>
            <h2 className="mt-2 break-words text-2xl font-semibold text-white">{note.problemTag}</h2>
          </div>
          <div className="rounded-full border border-white/10 bg-night px-3 py-2 text-xs text-text-secondary">
            {formatRelativeTime(note.createdAt)}
          </div>
        </div>
        <p className="wrap-anywhere text-sm leading-7 text-text-secondary">{clipText(note.problemDescription, 220)}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
          <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">What looks wrong</p>
          <p className="wrap-anywhere mt-3 text-sm leading-7 text-white">{diagnosisPreview}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
          <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">
            {note.unsolved ? 'Latest failure' : 'Likely fix'}
          </p>
          <p className="wrap-anywhere mt-3 text-sm leading-7 text-white">{solutionPreview}</p>
        </div>
      </div>

      <div className="grid gap-3 grid-cols-2">
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Score</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(note.score)}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Usage</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(note.usageCount)}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Attempts</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(note.attemptCount)}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Models tried</p>
          <p className="mt-2 text-2xl font-semibold text-white">
            {formatCompactNumber(note.attemptedModels.length || note.failedModelCount)}
          </p>
        </div>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Quick attempt overview</p>
            <p className="mt-2 text-sm text-text-secondary">
              Latest recorded repair tries. Open the issue pages for the full chain.
            </p>
          </div>
          <span className="rounded-full border border-white/10 bg-night px-3 py-2 text-xs text-text-secondary">
            {recentAttempts.length} shown
          </span>
        </div>

        <div className="mt-4 space-y-3">
          {recentAttempts.length ? (
            recentAttempts.map((attempt, index) => (
              <div key={`${attempt.timestamp}-${index}`} className="rounded-2xl border border-white/8 bg-night/80 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-white">{attempt.model ?? 'Unknown model'}</p>
                    <p className="text-xs uppercase tracking-[0.25em] text-text-secondary">
                      {formatRelativeTime(attempt.timestamp)}
                    </p>
                  </div>
                  <div className="text-right text-xs text-text-secondary">
                    <p>
                      {attempt.tokenCount == null
                        ? 'Tokens unknown'
                        : `${formatCompactNumber(attempt.tokenCount)} tokens`}
                    </p>
                    <p>{attempt.outcome ?? 'Outcome not captured.'}</p>
                  </div>
                </div>
                <div className="mt-3 space-y-2 text-sm text-white">
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Thought</p>
                    <p className="wrap-anywhere mt-1">{clipText(attempt.thought, 150)}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Tried</p>
                    <p className="wrap-anywhere mt-1">{clipText(attempt.tried, 150)}</p>
                  </div>
                </div>
              </div>
            ))
          ) : (
            <p className="text-sm text-text-secondary">No recorded model attempts yet.</p>
          )}
        </div>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
        <div className="flex items-center gap-2 text-sm uppercase tracking-[0.3em] text-text-secondary">
          <Sparkles size={15} />
          Environment snapshot
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <span className="rounded-full border border-white/10 bg-night px-3 py-2 text-sm text-white">
            {note.environment.os}
          </span>
          <span className="wrap-anywhere rounded-full border border-white/10 bg-night px-3 py-2 text-sm text-white">
            {note.environment.languageVersion}
          </span>
          {note.environment.libraries.slice(0, 4).map((library) => (
            <span
              key={library}
              className="max-w-full break-all rounded-full border border-white/10 bg-night px-3 py-2 text-sm text-white"
            >
              {library}
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        {onOpenNotePage ? (
          <Button variant="secondary" onClick={onOpenNotePage}>
            Open note page
            <ArrowUpRight className="ml-2" size={16} />
          </Button>
        ) : null}
        {onOpenErrorPage ? (
          <Button onClick={onOpenErrorPage}>
            <Route className="mr-2" size={16} />
            Open error page
            <ArrowUpRight className="ml-2" size={16} />
          </Button>
        ) : null}
      </div>
    </div>
  )
}
