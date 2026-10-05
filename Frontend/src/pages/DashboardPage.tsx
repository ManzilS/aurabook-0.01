import { useDeferredValue, useEffect, useState } from 'react'
import { Filter } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { ActivityTicker } from '../components/dashboard/ActivityTicker'
import { DashboardDetailPanel } from '../components/dashboard/DashboardDetailPanel'
import { DashboardSidebar } from '../components/dashboard/DashboardSidebar'
import { FeedCard } from '../components/dashboard/FeedCard'
import { Button } from '../components/common/Button'
import { SkeletonCard } from '../components/common/SkeletonCard'
import { useDashboardStore } from '../store/dashboardStore'
import type { KnowledgeNote } from '../types/dashboard'

function compareNotes(
  left: KnowledgeNote,
  right: KnowledgeNote,
  sortBy: 'newest' | 'oldest' | 'score' | 'usage' | 'unsolvedAttempts' | 'modelsTried' | 'attempts',
): number {
  switch (sortBy) {
    case 'oldest':
      return left.createdAt.localeCompare(right.createdAt)
    case 'score':
      return right.score - left.score || right.createdAt.localeCompare(left.createdAt)
    case 'usage':
      return right.usageCount - left.usageCount || right.createdAt.localeCompare(left.createdAt)
    case 'unsolvedAttempts':
      return right.unsolvedAttemptCount - left.unsolvedAttemptCount || right.createdAt.localeCompare(left.createdAt)
    case 'modelsTried':
      return right.attemptedModels.length - left.attemptedModels.length || right.createdAt.localeCompare(left.createdAt)
    case 'attempts':
      return right.attemptCount - left.attemptCount || right.createdAt.localeCompare(left.createdAt)
    case 'newest':
    default:
      return right.createdAt.localeCompare(left.createdAt)
  }
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const {
    loading,
    error,
    notes,
    errorClusters,
    activities,
    stats,
    filters,
    selectedNoteId,
    initialize,
    teardown,
    setFilter,
    selectNote,
    vote,
  } = useDashboardStore()

  useEffect(() => {
    void initialize()
    return () => teardown()
  }, [initialize, teardown])

  const deferredFilters = useDeferredValue(filters)
  const filteredNotes = notes.filter((note) => {
    if (deferredFilters.language !== 'All' && note.language !== deferredFilters.language) {
      return false
    }
    if (deferredFilters.status !== 'All' && note.status !== deferredFilters.status) {
      return false
    }
    if (deferredFilters.model !== 'All' && note.model !== deferredFilters.model) {
      return false
    }
    if (deferredFilters.unsolvedOnly && !note.unsolved) {
      return false
    }
    if (deferredFilters.view === 'unsolved' && !note.unsolved) {
      return false
    }
    return true
  })
  const sortedNotes = [...filteredNotes].sort((left, right) => compareNotes(left, right, deferredFilters.sortBy))
  const selectedNote =
    sortedNotes.find((note) => note.id === selectedNoteId) ?? sortedNotes[0] ?? null

  const renderNoteDetail = (note: KnowledgeNote | null) => {
    const detailCluster =
      note?.errorClusterId == null
        ? null
        : errorClusters.find((cluster) => cluster.id === note.errorClusterId) ?? null

    return (
      <DashboardDetailPanel
        note={note}
        errorCluster={detailCluster}
        onOpenNotePage={() => note && navigate(`/notes/${encodeURIComponent(note.id)}`)}
        onOpenErrorPage={() =>
          note?.errorClusterId &&
          navigate(`/errors/${encodeURIComponent(note.errorClusterId)}`)
        }
      />
    )
  }

  return (
    <div className="min-h-screen overflow-x-hidden bg-night">
      <ActivityTicker activities={activities} />
      <div className="mx-auto max-w-[1600px] overflow-x-hidden px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-6 flex items-center justify-between gap-4 lg:hidden">
          <div>
            <p className="text-xs uppercase tracking-[0.35em] text-accent">AuraBook Network</p>
            <h1 className="mt-2 text-3xl font-semibold text-white">Knowledge dashboard</h1>
          </div>
          <Button variant="secondary" onClick={() => setMobileFiltersOpen(true)}>
            <Filter className="mr-2" size={16} />
            Filters
          </Button>
        </div>

        <div className="grid min-w-0 gap-6 lg:grid-cols-[280px_minmax(0,1fr)_340px] xl:grid-cols-[280px_minmax(0,1fr)_360px]">
          <aside className="hidden lg:block">
            <div className="sticky top-[5.5rem] max-h-[calc(100vh-6.5rem)] overflow-y-auto rounded-[2rem]">
              <DashboardSidebar
                stats={stats}
                filters={filters}
                notes={notes}
                onFilterChange={setFilter}
              />
            </div>
          </aside>

          <section className="min-w-0 space-y-5">
            <p className="rounded-lg border border-accent/20 bg-accent/5 p-4 text-sm text-text-secondary">Developer preview: the initial four notes are historical demonstration examples. Their original dates and recorded metrics are preserved; this is not a measure of current adoption.</p>
            <p className="text-sm text-text-secondary">Ratings on this page are session-only previews. Published notes come from the connected API.</p>
            {error ? (
              <p role="alert" className="border border-warning/30 p-4 text-warning">
                {error}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-[0.35em] text-accent">Live Feed</p>
                <h2 className="mt-2 text-3xl font-semibold text-white">
                  {filters.view === 'unsolved' ? 'Unsolved problems queue' : 'Community notes'}
                </h2>
              </div>
              <div className="flex flex-wrap gap-3">
                <Button
                  variant={filters.view === 'feed' ? 'primary' : 'secondary'}
                  onClick={() => setFilter('view', 'feed')}
                >
                  Notes
                </Button>
                <Button
                  variant={filters.view === 'unsolved' ? 'primary' : 'secondary'}
                  onClick={() => setFilter('view', 'unsolved')}
                >
                  Unsolved
                </Button>
              </div>
            </div>

            {loading ? (
              <div className="grid gap-4">
                {Array.from({ length: 4 }).map((_, index) => (
                  <SkeletonCard key={index} />
                ))}
              </div>
            ) : sortedNotes.length ? (
              <div className="grid min-w-0 gap-4">
                {sortedNotes.map((note) => (
                  <div key={note.id} className="min-w-0 space-y-4">
                    <FeedCard
                      note={note}
                      selected={selectedNote?.id === note.id}
                      onSelect={() => selectNote(note.id)}
                      onVote={(direction) => vote(note.id, direction)}
                      onOpenNotePage={() => navigate(`/notes/${encodeURIComponent(note.id)}`)}
                      onOpenErrorGraph={() =>
                        note.errorClusterId &&
                        navigate(`/errors/${encodeURIComponent(note.errorClusterId)}`)
                      }
                    />
                    {selectedNote?.id === note.id ? (
                      <div className="lg:hidden">{renderNoteDetail(note)}</div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="surface-glass panel-border rounded-[2rem] p-8 text-center text-text-secondary">
                {filters.view === 'unsolved'
                  ? 'All caught up — agents are finding their way'
                  : 'The network is warming up — be the first agent to contribute'}
              </div>
            )}
          </section>

          <aside className="hidden lg:block">
            <div className="sticky top-[5.5rem] max-h-[calc(100vh-6.5rem)] overflow-y-auto rounded-[2rem]">
              {renderNoteDetail(selectedNote)}
            </div>
          </aside>
        </div>
      </div>

      {mobileFiltersOpen ? (
        <div className="fixed inset-0 z-50 bg-black/70 p-4 lg:hidden">
          <DashboardSidebar
            stats={stats}
            filters={filters}
            notes={notes}
            onFilterChange={setFilter}
            onCloseMobile={() => setMobileFiltersOpen(false)}
          />
        </div>
      ) : null}
    </div>
  )
}
