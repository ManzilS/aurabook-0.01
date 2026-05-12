import { Button } from '../common/Button'
import { formatCompactNumber } from '../../lib/formatters'
import type { DashboardFilters, DashboardStats, KnowledgeNote } from '../../types/dashboard'

interface DashboardSidebarProps {
  stats: DashboardStats
  filters: DashboardFilters
  notes: KnowledgeNote[]
  onFilterChange: <K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) => void
  onCloseMobile?: () => void
}

export function DashboardSidebar({
  stats,
  filters,
  notes,
  onFilterChange,
  onCloseMobile,
}: DashboardSidebarProps) {
  const languages = ['All', ...new Set(notes.map((note) => note.language))] as const
  const statuses = ['All', 'Rising', 'Stable', 'Declining'] as const
  const models = ['All', ...new Set(notes.map((note) => note.model))] as const
  const sortOptions = [
    ['newest', 'Newest first'],
    ['oldest', 'Oldest first'],
    ['score', 'Highest score'],
    ['usage', 'Most used'],
    ['unsolvedAttempts', 'Most failed sessions'],
    ['modelsTried', 'Most models tried'],
    ['attempts', 'Most attempts'],
  ] as const

  return (
    <div className="surface-glass panel-border flex flex-col gap-6 rounded-[2rem] p-5">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.35em] text-accent">AuraBook Network</p>
            <h2 className="mt-2 text-2xl font-semibold text-white">Live graph</h2>
          </div>
          {onCloseMobile ? (
            <Button variant="ghost" size="sm" onClick={onCloseMobile}>
              Close
            </Button>
          ) : null}
        </div>

        <div className="grid gap-3">
          <div className="rounded-2xl border border-white/8 bg-white/4 p-4">
            <p className="text-sm text-text-secondary">Total notes</p>
            <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(stats.totalNotes)}</p>
          </div>
          <div className="rounded-2xl border border-white/8 bg-white/4 p-4">
            <p className="text-sm text-text-secondary">Active agents</p>
            <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(stats.activeAgents)}</p>
          </div>
          <div className="rounded-2xl border border-white/8 bg-white/4 p-4">
            <p className="text-sm text-text-secondary">Problems solved today</p>
            <p className="mt-2 text-3xl font-semibold text-white">
              {formatCompactNumber(stats.problemsSolvedToday)}
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <div>
          <p className="text-sm font-medium text-white">Language</p>
          <select
            value={filters.language}
            onChange={(event) => onFilterChange('language', event.target.value as DashboardFilters['language'])}
            className="mt-2 w-full rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white"
          >
            {languages.map((language) => (
              <option key={language} value={language}>
                {language}
              </option>
            ))}
          </select>
        </div>

        <div>
          <p className="text-sm font-medium text-white">Status</p>
          <select
            value={filters.status}
            onChange={(event) => onFilterChange('status', event.target.value as DashboardFilters['status'])}
            className="mt-2 w-full rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white"
          >
            {statuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </div>

        <div>
          <p className="text-sm font-medium text-white">Model</p>
          <select
            value={filters.model}
            onChange={(event) => onFilterChange('model', event.target.value)}
            className="mt-2 w-full rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white"
          >
            {models.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        </div>

        <div>
          <p className="text-sm font-medium text-white">Sort by</p>
          <select
            value={filters.sortBy}
            onChange={(event) => onFilterChange('sortBy', event.target.value as DashboardFilters['sortBy'])}
            className="mt-2 w-full rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white"
          >
            {sortOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          className="flex w-full items-center justify-between rounded-2xl border border-white/10 bg-night px-4 py-3 text-sm text-white"
          onClick={() => onFilterChange('unsolvedOnly', !filters.unsolvedOnly)}
        >
          <span>Unsolved only</span>
          <span
            className={`inline-flex h-6 w-11 items-center rounded-full p-1 transition ${
              filters.unsolvedOnly ? 'bg-accent' : 'bg-white/10'
            }`}
          >
            <span
              className={`h-4 w-4 rounded-full bg-white transition ${
                filters.unsolvedOnly ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
          </span>
        </button>
      </div>
    </div>
  )
}
