import { LoaderCircle, TriangleAlert, CheckCircle2, Sparkles } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { cn } from '../../lib/cn'
import type { ReplayPanelState, TerminalEntry } from '../../types/demo'
import { TypewriterLine } from './TypewriterLine'

interface TerminalPanelProps {
  panel: ReplayPanelState
}

function lineTone(kind: TerminalEntry['kind']): string {
  if (kind === 'error') {
    return 'text-danger'
  }
  if (kind === 'note') {
    return 'rounded-2xl border border-accent/20 bg-accent/10 p-3 text-accent'
  }
  if (kind === 'runtime') {
    return 'border-success/20 bg-success/10 text-success'
  }
  if (kind === 'success') {
    return 'text-success'
  }
  return 'text-white'
}

function entryLabel(kind: TerminalEntry['kind']): string {
  if (kind === 'success') {
    return 'Provider response'
  }
  if (kind === 'error') {
    return 'Error'
  }
  if (kind === 'note') {
    return 'AuraBook system note'
  }
  if (kind === 'runtime') {
    return 'Python runtime'
  }
  if (kind === 'status') {
    return 'Runtime status'
  }
  return 'Live step'
}

export function TerminalPanel({ panel }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'smooth' })
  }, [panel.entries.length])

  return (
    <div className="surface-glass panel-border flex h-[36rem] flex-col overflow-hidden rounded-[2rem] shadow-panel">
      <div className={cn('border-b border-white/8 bg-gradient-to-r px-5 py-4', panel.accentClass)}>
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.35em] text-text-secondary">Terminal replay</p>
            <h3 className="text-lg font-semibold text-white">{panel.title}</h3>
          </div>
          <div className="rounded-full border border-white/10 bg-night/70 px-3 py-2 font-mono text-sm text-white">
            {panel.tokenCount.toLocaleString()} tokens
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between border-b border-white/8 px-5 py-3 text-sm text-text-secondary">
        <div className="flex items-center gap-2">
          {panel.statusLabel.toLowerCase().includes('error') || panel.statusLabel.toLowerCase().includes('failed') ? (
            <TriangleAlert className="text-danger" size={16} />
          ) : panel.complete ? (
            <CheckCircle2 className="text-success" size={16} />
          ) : panel.entries.length > 0 ? (
            <LoaderCircle className="animate-spin text-warning" size={16} />
          ) : (
            <Sparkles className="text-accent" size={16} />
          )}
          <span>{panel.statusLabel}</span>
        </div>
        {panel.complete ? (
          <span className="font-medium text-success">Final token count: {panel.tokenCount.toLocaleString()}</span>
        ) : null}
      </div>

      <div ref={containerRef} className="flex-1 space-y-3 overflow-y-auto bg-[#090909] px-5 py-5 font-mono text-sm">
        {panel.entries.length === 0 ? (
          <p className="text-text-secondary">$ Waiting for replay...</p>
        ) : null}
        {panel.entries.map((entry) => (
          <div
            key={entry.id}
            className={cn(
              'animate-feed-in max-w-full rounded-2xl border border-white/5 bg-white/[0.02] p-3',
              lineTone(entry.kind),
            )}
          >
            <div className="mb-2 flex items-center justify-between gap-3 text-[0.68rem] uppercase tracking-[0.22em] text-white/45">
              <span>{entryLabel(entry.kind)}</span>
              <span>{entry.timestamp}</span>
            </div>
            {entry.kind === 'error' ? (
              <div className="flex items-start gap-2">
                <TriangleAlert className="mt-0.5 shrink-0" size={16} />
                <TypewriterLine text={entry.text} />
              </div>
            ) : entry.kind === 'note' ? (
              <TypewriterLine text={entry.text} />
            ) : (
              <TypewriterLine text={entry.text} />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
