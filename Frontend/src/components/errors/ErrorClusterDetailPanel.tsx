import { ArrowUpRight, Download, GitBranch, Route } from 'lucide-react'
import { Button } from '../common/Button'
import { ErrorChainMap } from './ErrorChainMap'
import { formatCompactNumber, formatRelativeTime } from '../../lib/formatters'
import type { ErrorChainStep, ErrorCluster } from '../../types/dashboard'

interface ErrorClusterDetailPanelProps {
  cluster: ErrorCluster | null
  onDownload: () => void
  onOpenIssuePage?: () => void
  onOpenNotePage?: () => void
}

function depthPadding(depth: number): string {
  const clamped = Math.min(Math.max(depth, 0), 6)
  return ['pl-0', 'pl-4', 'pl-8', 'pl-12', 'pl-16', 'pl-20', 'pl-24'][clamped] ?? 'pl-24'
}

function stepAccent(step: ErrorChainStep): string {
  if (step.kind === 'solution') {
    return 'border-success/30 bg-success/10'
  }
  if (step.kind === 'derived_error' || step.kind === 'root_error') {
    return 'border-warning/25 bg-warning/10'
  }
  return 'border-white/8 bg-night/80'
}

export function ErrorClusterDetailPanel({
  cluster,
  onDownload,
  onOpenIssuePage,
  onOpenNotePage,
}: ErrorClusterDetailPanelProps) {
  if (!cluster) {
    return (
      <div className="surface-glass panel-border flex min-h-[32rem] items-center justify-center rounded-[2rem] p-8 text-center text-text-secondary">
        Select an error cluster to inspect the chain of failures and repairs.
      </div>
    )
  }

  return (
    <div className="surface-glass panel-border flex min-h-[32rem] min-w-0 flex-col gap-6 overflow-hidden rounded-[2rem] p-5">
      <div className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-xs uppercase tracking-[0.35em] text-accent">Error graph</p>
            <h2 className="mt-2 break-words text-2xl font-semibold text-white">{cluster.problemTag}</h2>
          </div>
          <div className="rounded-full border border-white/10 bg-night px-3 py-2 text-xs text-text-secondary">
            {formatRelativeTime(cluster.updatedAt)}
          </div>
        </div>
        <p className="wrap-anywhere text-sm text-text-secondary">{cluster.diagnosisSummary}</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Root error</p>
          <p className="wrap-anywhere mt-3 whitespace-pre-line text-sm leading-7 text-white">{cluster.rootErrorDetail}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Diagnosis</p>
          <p className="wrap-anywhere mt-3 whitespace-pre-line text-sm leading-7 text-white">{cluster.fullDiagnosis}</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Attempts</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(cluster.attemptCount)}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Failures</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(cluster.failureCount)}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Cascade errors</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(cluster.cascadeErrorCount)}</p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-white/4 p-4">
          <p className="text-sm text-text-secondary">Models tried</p>
          <p className="mt-2 text-2xl font-semibold text-white">{formatCompactNumber(cluster.attemptedModels.length)}</p>
        </div>
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Path map</p>
            <p className="mt-2 text-sm text-text-secondary">
              Visual trace of the route the model took through the repair loop.
            </p>
          </div>
          <span className="text-sm text-text-secondary">{cluster.steps.length} nodes</span>
        </div>
        <ErrorChainMap steps={cluster.steps} variant="full" />
      </div>

      <div className="rounded-[1.5rem] border border-white/8 bg-night/70 p-4">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm uppercase tracking-[0.3em] text-text-secondary">
            <Route size={16} />
            Linked error chain
          </div>
          <span className="text-sm text-text-secondary">{cluster.steps.length} linked steps</span>
        </div>

        <div className="space-y-3">
          {cluster.steps.map((step) => (
            <div key={step.id} className={depthPadding(step.depth)}>
              <div className={`rounded-2xl border p-4 ${stepAccent(step)}`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-white">{step.title}</p>
                    <p className="text-xs uppercase tracking-[0.25em] text-text-secondary">
                      {step.model ?? step.kind.replace('_', ' ')}
                    </p>
                  </div>
                  <div className="text-right text-xs text-text-secondary">
                    <p>{formatRelativeTime(step.timestamp)}</p>
                    <p>
                      {step.tokenCount == null ? 'Tokens unknown' : `${formatCompactNumber(step.tokenCount)} tokens`}
                    </p>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 text-sm text-white">
                  {step.diagnosis ? (
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">What the AI thought was wrong</p>
                      <p className="wrap-anywhere mt-1 whitespace-pre-line">{step.diagnosis}</p>
                    </div>
                  ) : null}
                  {step.action ? (
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">What it tried</p>
                      <p className="wrap-anywhere mt-1 whitespace-pre-line">{step.action}</p>
                    </div>
                  ) : null}
                  {step.errorDetail ? (
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Error returned</p>
                      <p className="wrap-anywhere mt-1 whitespace-pre-line">{step.errorDetail}</p>
                    </div>
                  ) : null}
                  {step.outcome ? (
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Outcome</p>
                      <p className="wrap-anywhere mt-1 whitespace-pre-line">{step.outcome}</p>
                    </div>
                  ) : null}
                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Commands</p>
                      <p className="wrap-anywhere mt-1 font-mono text-xs text-text-secondary">
                        {step.commands.join(' | ') || 'No commands recorded.'}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.2em] text-text-secondary">Changed files</p>
                      <p className="wrap-anywhere mt-1 font-mono text-xs text-text-secondary">
                        {step.changedFiles.join(', ') || 'No file changes recorded.'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" onClick={onDownload}>
          <Download className="mr-2" size={16} />
          Download error chain
        </Button>
        {onOpenIssuePage ? (
          <Button onClick={onOpenIssuePage}>
            Open error page
            <ArrowUpRight className="ml-2" size={16} />
          </Button>
        ) : null}
        {cluster.noteId && onOpenNotePage ? (
          <Button variant="secondary" onClick={onOpenNotePage}>
            Open note page
            <ArrowUpRight className="ml-2" size={16} />
          </Button>
        ) : null}
        <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm text-text-secondary">
          <GitBranch size={14} />
          {cluster.relatedTrajectoryIds.length} related trajectories
        </div>
      </div>
    </div>
  )
}
