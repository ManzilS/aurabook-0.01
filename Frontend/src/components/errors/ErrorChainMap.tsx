import { Bot, CheckCircle2, CircleAlert, GitBranch } from 'lucide-react'
import { cn } from '../../lib/cn'
import type { ErrorChainStep } from '../../types/dashboard'

interface ErrorChainMapProps {
  steps: ErrorChainStep[]
  variant?: 'compact' | 'full'
  className?: string
}

function humanizeKind(kind: ErrorChainStep['kind']): string {
  if (kind === 'root_error') {
    return 'Root error'
  }
  if (kind === 'derived_error') {
    return 'Returned error'
  }
  if (kind === 'solution') {
    return 'Solution'
  }
  return 'Attempt'
}

function accentClasses(kind: ErrorChainStep['kind']): string {
  if (kind === 'solution') {
    return 'border-success/35 bg-success/10 text-success'
  }
  if (kind === 'derived_error' || kind === 'root_error') {
    return 'border-warning/35 bg-warning/10 text-warning'
  }
  return 'border-accent/25 bg-accent/10 text-accent'
}

function excerptForStep(step: ErrorChainStep): string {
  return step.errorExcerpt ?? step.diagnosis ?? step.action ?? step.outcome ?? 'No detail captured.'
}

function indentForDepth(depth: number, variant: 'compact' | 'full'): number {
  const step = variant === 'compact' ? 16 : 24
  const maxDepth = variant === 'compact' ? 3 : 4
  return Math.min(depth, maxDepth) * step
}

export function ErrorChainMap({
  steps,
  variant = 'full',
  className,
}: ErrorChainMapProps) {
  if (!steps.length) {
    return (
      <div
        className={cn(
          'rounded-[1.5rem] border border-white/8 bg-white/[0.03] p-5 text-sm text-text-secondary',
          className,
        )}
      >
        No linked path has been captured yet.
      </div>
    )
  }

  const metrics =
    variant === 'compact'
      ? {
          excerptLimit: 84,
          cardPadding: 'p-3',
          titleSize: 'text-sm',
          lineHeight: 'leading-5',
        }
      : {
          excerptLimit: 132,
          cardPadding: 'p-4',
          titleSize: 'text-base',
          lineHeight: 'leading-6',
        }

  const stepById = new Map(steps.map((step) => [step.id, step]))

  return (
    <div
      className={cn(
        'overflow-hidden rounded-[1.5rem] border border-white/8 bg-night/70 p-3',
        className,
      )}
    >
      <div className="space-y-3">
        {steps.map((step) => {
          const Icon =
            step.kind === 'solution'
              ? CheckCircle2
              : step.kind === 'derived_error' || step.kind === 'root_error'
                ? CircleAlert
                : Bot
          const excerpt = excerptForStep(step)
          const clipped =
            excerpt.length > metrics.excerptLimit
              ? `${excerpt.slice(0, metrics.excerptLimit - 1).trimEnd()}…`
              : excerpt
          const indent = indentForDepth(step.depth, variant)
          const parent = step.parentStepId ? stepById.get(step.parentStepId) ?? null : null

          return (
            <div key={step.id} className="relative min-w-0" style={{ paddingLeft: `${indent}px` }}>
              {step.depth > 0 ? (
                <>
                  <div
                    aria-hidden="true"
                    className="absolute bottom-0 top-0 w-px bg-accent/20"
                    style={{ left: `${Math.max(6, indent - 10)}px` }}
                  />
                  <div
                    aria-hidden="true"
                    className="absolute h-px bg-accent/25"
                    style={{
                      left: `${Math.max(6, indent - 10)}px`,
                      top: '28px',
                      width: '14px',
                    }}
                  />
                  <div
                    aria-hidden="true"
                    className="absolute h-2.5 w-2.5 rounded-full border border-accent/30 bg-night"
                    style={{ left: `${Math.max(2, indent - 14)}px`, top: '23px' }}
                  />
                </>
              ) : null}

              <div
                className={cn(
                  'min-w-0 rounded-[1.25rem] border shadow-[0_16px_40px_rgba(0,0,0,0.28)]',
                  metrics.cardPadding,
                  accentClasses(step.kind),
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-words text-[11px] uppercase tracking-[0.22em] text-text-secondary">
                      {humanizeKind(step.kind)}
                    </p>
                    <p className={cn('mt-1 break-words font-semibold text-white', metrics.titleSize)}>
                      {step.title}
                    </p>
                    {parent ? (
                      <p className="mt-1 text-xs text-text-secondary">
                        from {parent.title}
                      </p>
                    ) : null}
                  </div>
                  <Icon size={16} className="shrink-0" />
                </div>

                <p className={cn('mt-3 break-words text-sm text-text-secondary', metrics.lineHeight)}>
                  {clipped}
                </p>
              </div>
            </div>
          )
        })}

        <div className="pointer-events-none inline-flex items-center gap-2 rounded-full border border-white/10 bg-night/80 px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-text-secondary">
          <GitBranch size={12} />
          Path map
        </div>
      </div>
    </div>
  )
}
