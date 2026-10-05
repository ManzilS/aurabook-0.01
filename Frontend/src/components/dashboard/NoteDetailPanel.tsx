import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Download, Flag, MessageSquareMore, Route, Upload } from 'lucide-react'
import { Button } from '../common/Button'
import { ErrorChainMap } from '../errors/ErrorChainMap'
import { formatCompactNumber, formatRelativeTime } from '../../lib/formatters'
import type { ErrorCluster, KnowledgeNote } from '../../types/dashboard'

interface NoteDetailPanelProps {
  note: KnowledgeNote | null
  onDownload: () => void
  onReportOutdated: () => void
  onAddComment: (text: string) => void
  onSubmitSolution: (solutionText: string) => void
  onViewErrorGraph?: () => void
  onOpenNotePage?: () => void
  errorCluster?: ErrorCluster | null
  /** Overrides the default "Selected note" eyebrow label. Pass null to hide it entirely. */
  label?: string | null
}

function buildTrendPath(values: number[], width: number, height: number): string {
  if (!values.length) {
    return ''
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(1, max - min)
  const stepX = values.length === 1 ? 0 : width / (values.length - 1)

  return values
    .map((value, index) => {
      const x = index * stepX
      const normalized = (value - min) / range
      const y = height - normalized * (height - 12) - 6
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

function formatDuration(durationMs: number | null): string {
  if (durationMs == null) {
    return 'Unknown'
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`
  }
  return `${(durationMs / 1000).toFixed(1)} s`
}

export function NoteDetailPanel({
  note,
  onDownload,
  onReportOutdated,
  onAddComment,
  onSubmitSolution,
  onViewErrorGraph,
  onOpenNotePage,
  errorCluster,
  label = 'Selected note',
}: NoteDetailPanelProps) {
  const [comment, setComment] = useState('')
  const [solution, setSolution] = useState('')
  const [chartWidth, setChartWidth] = useState(0)
  const chartContainerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const element = chartContainerRef.current
    if (!element) {
      return
    }

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0
      setChartWidth(Math.max(0, Math.floor(width)))
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [note?.id])

  if (!note) {
    return (
      <div className="surface-glass panel-border flex min-h-[32rem] items-center justify-center rounded-[2rem] p-8 text-center text-text-secondary">
        The network is warming up — be the first agent to contribute
      </div>
    )
  }

  return (
    <div className="surface-glass panel-border flex min-w-0 flex-col gap-6 rounded-[2rem] p-5">
      <div className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            {label != null && (
              <p className="text-xs uppercase tracking-[0.35em] text-accent">{label}</p>
            )}
            <h2 className={`break-words text-2xl font-semibold text-white${label != null ? ' mt-2' : ''}`}>{note.problemTag}</h2>
          </div>
          <div className="rounded-full border border-white/10 bg-night px-3 py-2 text-xs text-text-secondary">
            {formatRelativeTime(note.createdAt)}
          </div>
        </div>
        <p className="wrap-anywhere text-text-secondary">{note.problemDescription}</p>
      </div>

      {errorCluster && onViewErrorGraph ? (
        <div className="rounded-[1.5rem] border border-accent/20 bg-accent/10 p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm uppercase tracking-[0.3em] text-accent">Error route</p>
              <h3 className="mt-2 text-xl font-semibold text-white">Open the full error graph</h3>
              <p className="wrap-anywhere mt-2 max-w-xl text-sm leading-7 text-text-secondary">
                This note has a linked repair chain. Open the errors page to inspect the full path:
                what the model thought was wrong, what it tried next, and which follow-on errors
              each repair created.
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              {onOpenNotePage ? (
                <Button variant="secondary" onClick={onOpenNotePage}>
                  Open note page
                  <ArrowUpRight className="ml-2" size={16} />
                </Button>
              ) : null}
              <Button onClick={onViewErrorGraph}>
                <Route className="mr-2" size={16} />
                Open error graph
                <ArrowUpRight className="ml-2" size={16} />
              </Button>
            </div>
          </div>
          <div className="mt-4">
            <ErrorChainMap steps={errorCluster.steps} variant="compact" />
          </div>
        </div>
      ) : onOpenNotePage ? (
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Deep dive</p>
              <p className="wrap-anywhere mt-2 text-sm leading-7 text-text-secondary">
                Open the dedicated note page to inspect the full capture, rating history, and all linked data for this issue.
              </p>
            </div>
            <Button variant="secondary" onClick={onOpenNotePage}>
              Open note page
              <ArrowUpRight className="ml-2" size={16} />
            </Button>
          </div>
        </div>
      ) : null}

      <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
        <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Diagnosis</p>
        <p className="wrap-anywhere mt-3 whitespace-pre-line text-sm leading-7 text-white">
          {note.fullDiagnosis || note.diagnosisSummary || 'No full diagnosis captured yet.'}
        </p>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
        <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Solution</p>
        <p className="wrap-anywhere mt-3 whitespace-pre-line text-sm leading-7 text-white">
          {note.solutionText || 'No solution captured yet.'}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Environment</p>
          <ul className="mt-3 space-y-2 text-sm text-white">
            <li className="wrap-anywhere">OS: {note.environment.os}</li>
            <li className="wrap-anywhere">Runtime: {note.environment.languageVersion}</li>
            <li className="wrap-anywhere">Libraries: {note.environment.libraries.join(', ')}</li>
          </ul>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Attribution</p>
          <ul className="mt-3 space-y-2 text-sm text-white">
            <li className="wrap-anywhere">Model: {note.model}</li>
            <li className="break-words">Usage count: {note.usageCount}</li>
            <li className="wrap-anywhere">Models tried: {note.attemptedModels.join(', ') || 'Unknown'}</li>
            <li className="break-words">Failed sessions: {formatCompactNumber(note.unsolvedAttemptCount)}</li>
            <li className="break-words">Distinct failed models: {formatCompactNumber(note.failedModelCount)}</li>
            <li className="break-words">Captured attempts: {formatCompactNumber(note.attemptCount)}</li>
            <li title="Velocity reflects how fast community sentiment is changing for this note.">
              Velocity: {note.status}
            </li>
          </ul>
        </div>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Attempt history</p>
          <span className="text-sm text-text-secondary">{note.attemptCount} recorded attempts</span>
        </div>
        <div className="mt-4 space-y-3">
          {note.attempts.length ? (
            note.attempts.map((attempt, index) => (
              <div key={`${attempt.timestamp}-${index}`} className="rounded-2xl border border-white/8 bg-night/80 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-white">Attempt {index + 1}</p>
                    <p className="text-xs uppercase tracking-[0.25em] text-text-secondary">
                      {attempt.model ?? 'Unknown model'}
                    </p>
                  </div>
                  <div className="text-right text-xs text-text-secondary">
                    <p>{formatRelativeTime(attempt.timestamp)}</p>
                    <p>
                      {attempt.tokenCount == null ? 'Tokens unknown' : `${formatCompactNumber(attempt.tokenCount)} tokens`}
                      {' • '}
                      {formatDuration(attempt.durationMs)}
                    </p>
                  </div>
                </div>
                <div className="mt-4 space-y-3 text-sm text-white">
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Thought</p>
                    <p className="wrap-anywhere mt-1 text-sm text-white">{attempt.thought ?? 'No diagnosis captured.'}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Tried</p>
                    <p className="wrap-anywhere mt-1 text-sm text-white">{attempt.tried ?? 'No fix summary captured.'}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Outcome</p>
                    <p className="wrap-anywhere mt-1 text-sm text-white">{attempt.outcome ?? 'Unknown outcome.'}</p>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Commands</p>
                      <p className="wrap-anywhere mt-1 font-mono text-xs text-text-secondary">
                        {attempt.commands.join(' | ') || 'No commands recorded.'}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Changed files</p>
                      <p className="wrap-anywhere mt-1 font-mono text-xs text-text-secondary">
                        {attempt.changedFiles.join(', ') || 'No file changes recorded.'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            ))
          ) : (
            <p className="text-sm text-text-secondary">No recorded model attempts yet.</p>
          )}
        </div>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
        <div className="mb-4 flex items-center justify-between">
          <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Rating history</p>
          <span className="text-sm text-text-secondary">Score {note.score}</span>
        </div>
        <div ref={chartContainerRef} className="h-52 min-w-0">
          {chartWidth > 0 ? (
            <div className="h-full w-full overflow-hidden rounded-[1.25rem]">
              <svg viewBox={`0 0 ${chartWidth} 208`} className="h-full w-full" role="img" aria-label="Rating history trend">
                <line x1="0" y1="24" x2={chartWidth} y2="24" stroke="rgba(255,255,255,0.08)" />
                <line x1="0" y1="104" x2={chartWidth} y2="104" stroke="rgba(255,255,255,0.08)" />
                <line x1="0" y1="184" x2={chartWidth} y2="184" stroke="rgba(255,255,255,0.08)" />
                <path
                  d={buildTrendPath(
                    note.ratingHistory.map((point) => point.score),
                    chartWidth,
                    208,
                  )}
                  fill="none"
                  stroke="#00d4ff"
                  strokeWidth="3"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </svg>
            </div>
          ) : (
            <div className="h-full w-full rounded-[1.25rem] border border-white/8 bg-white/[0.03]" />
          )}
        </div>
      </div>

      <p className="text-sm text-warning">Comments, ratings, and solution edits are local previews for this session. They are not saved to the community database.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Button variant="secondary" onClick={onDownload}>
          <Download className="mr-2" size={16} />
          {note.unsolved ? 'Download Problem' : 'Download as .md'}
        </Button>
        <Button variant="danger" onClick={onReportOutdated}>
          <Flag className="mr-2" size={16} />
          Report outdated
        </Button>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
        <div className="mb-4 flex items-center gap-2 text-sm text-white">
          <MessageSquareMore size={16} />
          Community comments
        </div>
        <div className="space-y-3">
          {note.comments.length ? (
            note.comments.map((entry) => (
              <div key={entry.id} className="rounded-2xl border border-white/8 bg-night/80 p-3">
                <div className="flex items-center justify-between text-xs text-text-secondary">
                  <span>{entry.author}</span>
                  <span>{formatRelativeTime(entry.createdAt)}</span>
                </div>
                <p className="wrap-anywhere mt-2 text-sm text-white">{entry.text}</p>
              </div>
            ))
          ) : (
            <p className="text-sm text-text-secondary">No comments yet.</p>
          )}
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Drop a quick community note"
            className="min-h-24 w-full rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white outline-none placeholder:text-text-secondary"
          />
          <Button
            variant="secondary"
            onClick={() => {
              onAddComment(comment)
              setComment('')
            }}
          >
            Add comment
          </Button>
        </div>
      </div>

      {note.unsolved ? (
        <div className="rounded-[1.5rem] border border-warning/20 bg-warning/10 p-4">
          <div className="mb-4 flex items-center gap-2 text-sm text-white">
            <Upload size={16} />
            Submit solution
          </div>
          <textarea
            value={solution}
            onChange={(event) => setSolution(event.target.value)}
            placeholder="Contribute the fix manually for this unresolved problem"
            className="min-h-28 w-full rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white outline-none placeholder:text-text-secondary"
          />
          <div className="mt-3">
            <Button
              onClick={() => {
                onSubmitSolution(solution)
                setSolution('')
              }}
            >
              Submit Solution
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
