import { ArrowDown, ArrowUp, Minus, Route, TriangleAlert } from 'lucide-react'
import { cn } from '../../lib/cn'
import { formatRelativeTime } from '../../lib/formatters'
import type { KnowledgeNote } from '../../types/dashboard'

interface FeedCardProps {
  note: KnowledgeNote
  selected: boolean
  onSelect: () => void
  onVote: (direction: 'up' | 'down') => void
  onOpenErrorGraph?: () => void
  onOpenNotePage?: () => void
}

function velocityIcon(velocity: KnowledgeNote['velocity']) {
  if (velocity === 'rising') {
    return <ArrowUp size={14} className="text-success" />
  }
  if (velocity === 'declining') {
    return <ArrowDown size={14} className="text-danger" />
  }
  return <Minus size={14} className="text-text-secondary" />
}

export function FeedCard({
  note,
  selected,
  onSelect,
  onVote,
  onOpenErrorGraph,
  onOpenNotePage,
}: FeedCardProps) {
  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        'surface-glass panel-border w-full max-w-full overflow-hidden rounded-[2rem] p-5 text-left transition hover:border-accent/25 hover:bg-white/[0.03]',
        'cursor-pointer focus:outline-none focus-visible:border-accent/40 focus-visible:shadow-glow',
        selected && 'border-accent/40 shadow-glow',
        note.recentlyUpdated && 'animate-feed-in',
      )}
    >
      <div className="flex flex-col items-start gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <span
              className="max-w-[20rem] truncate rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs uppercase tracking-[0.25em] text-text-secondary"
              title={note.problemTag}
            >
              {note.problemTag}
            </span>
            {note.unsolved ? (
              <span className="inline-flex items-center gap-2 rounded-full border border-warning/30 bg-warning/10 px-3 py-1 text-xs text-warning">
                <TriangleAlert size={12} />
                Unsolved
              </span>
            ) : null}
          </div>
          <p className="wrap-anywhere text-lg font-semibold text-white">{note.diagnosisSummary}</p>
        </div>

        <div className="shrink-0 rounded-full border border-white/10 bg-night/80 px-3 py-2 text-sm text-white">
          {velocityIcon(note.velocity)}
          <span className="ml-2">{note.status}</span>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-text-secondary">
        <span>{note.model}</span>
        <span>{formatRelativeTime(note.createdAt)}</span>
        <span>Usage {note.usageCount}</span>
        <span>{note.attemptedModels.length || note.failedModelCount} models tried</span>
        <span>{note.unsolvedAttemptCount} failed sessions</span>
      </div>

      <div className="mt-5 flex flex-col items-start gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              onVote('up')
            }}
            onKeyDown={(event) => event.stopPropagation()}
            className="rounded-full border border-white/10 bg-white/5 p-2 text-text-secondary transition hover:text-white"
            aria-label="Upvote note"
            title="Preview upvote for this session only; not saved"
          >
            <ArrowUp size={16} />
          </button>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              onVote('down')
            }}
            onKeyDown={(event) => event.stopPropagation()}
            className="rounded-full border border-white/10 bg-white/5 p-2 text-text-secondary transition hover:text-white"
            aria-label="Downvote note"
            title="Preview downvote for this session only; not saved"
          >
            <ArrowDown size={16} />
          </button>
          <span className="font-mono text-sm text-white">{note.score}</span>
        </div>

        <div className="flex min-w-0 w-full flex-wrap items-center gap-3 lg:w-auto lg:justify-end">
          {onOpenNotePage ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onOpenNotePage()
              }}
              onKeyDown={(event) => event.stopPropagation()}
              className="inline-flex max-w-full items-center gap-2 rounded-full border border-white/12 bg-white/5 px-3 py-2 text-xs uppercase tracking-[0.18em] text-white transition hover:border-white/25 hover:bg-white/10"
              aria-label="Open note page"
            >
              Note page
            </button>
          ) : null}
          {note.errorClusterId && onOpenErrorGraph ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onOpenErrorGraph()
              }}
              onKeyDown={(event) => event.stopPropagation()}
              className="inline-flex max-w-full items-center gap-2 rounded-full border border-accent/25 bg-accent/10 px-3 py-2 text-xs uppercase tracking-[0.18em] text-accent transition hover:border-accent/40 hover:bg-accent/15"
              aria-label="Open full error graph"
            >
              <Route size={14} />
              Error graph
            </button>
          ) : null}
          <span className="text-sm text-text-secondary">{note.language}</span>
        </div>
      </div>
    </article>
  )
}
