import { useEffect, useMemo } from 'react'
import { ArrowLeft, Route } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button } from '../components/common/Button'
import { SkeletonCard } from '../components/common/SkeletonCard'
import { NoteDetailPanel } from '../components/dashboard/NoteDetailPanel'
import { ErrorChainMap } from '../components/errors/ErrorChainMap'
import { IssueRecommendations } from '../components/issues/IssueRecommendations'
import { recommendationsForNote } from '../lib/issueRecommendations'
import { formatCompactNumber } from '../lib/formatters'
import { useDashboardStore } from '../store/dashboardStore'

export function NoteIssuePage() {
  const navigate = useNavigate()
  const { noteId } = useParams<{ noteId: string }>()
  const {
    loading,
    notes,
    errorClusters,
    initialize,
    teardown,
    addComment,
    reportOutdated,
    downloadNote,
    submitSolution,
  } = useDashboardStore()

  useEffect(() => {
    void initialize()
    return () => teardown()
  }, [initialize, teardown])

  const note = useMemo(
    () => notes.find((candidate) => candidate.id === noteId) ?? null,
    [noteId, notes],
  )
  const cluster = useMemo(
    () =>
      note?.errorClusterId == null
        ? null
        : errorClusters.find((candidate) => candidate.id === note.errorClusterId) ?? null,
    [errorClusters, note],
  )
  const recommendations = useMemo(
    () => (note ? recommendationsForNote(note, cluster) : []),
    [cluster, note],
  )

  if (loading && !note) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="grid gap-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    )
  }

  if (!note) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12 text-center sm:px-6 lg:px-8">
        <p className="text-xs uppercase tracking-[0.35em] text-accent">AuraBook Note</p>
        <h1 className="mt-4 text-4xl font-semibold text-white">Note not found</h1>
        <p className="mt-4 text-text-secondary">
          The requested note does not exist in the current dashboard snapshot.
        </p>
        <div className="mt-8">
          <Button variant="secondary" onClick={() => navigate('/dashboard')}>
            <ArrowLeft className="mr-2" size={16} />
            Back to dashboard
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen overflow-x-hidden bg-night">
      <div className="mx-auto max-w-[1360px] overflow-x-hidden px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-[0.35em] text-accent">AuraBook Note</p>
            <h1 className="mt-3 break-words text-4xl font-semibold text-white">{note.problemTag}</h1>
            <p className="mt-3 max-w-3xl text-text-secondary">
              Full note record, linked history, recommended actions, and all available metadata for this issue.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button variant="secondary" onClick={() => navigate('/dashboard')}>
              <ArrowLeft className="mr-2" size={16} />
              Back to dashboard
            </Button>
            {cluster ? (
              <Button onClick={() => navigate(`/errors/${encodeURIComponent(cluster.id)}`)}>
                <Route className="mr-2" size={16} />
                Open error page
              </Button>
            ) : null}
          </div>
        </div>

        <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <section className="min-w-0 space-y-6">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Usage count</p>
                <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(note.usageCount)}</p>
              </div>
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Attempts</p>
                <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(note.attemptCount)}</p>
              </div>
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Models tried</p>
                <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(note.attemptedModels.length)}</p>
              </div>
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Failed sessions</p>
                <p className="mt-2 text-3xl font-semibold text-white">
                  {formatCompactNumber(note.unsolvedAttemptCount)}
                </p>
              </div>
            </div>

            <NoteDetailPanel
              note={note}
              errorCluster={cluster}
              label={null}
              onDownload={() => downloadNote(note.id)}
              onReportOutdated={() => reportOutdated(note.id)}
              onAddComment={(text) => addComment(note.id, text)}
              onSubmitSolution={(solutionText) => submitSolution(note.id, solutionText)}
              onOpenNotePage={undefined}
              onViewErrorGraph={
                cluster ? () => navigate(`/errors/${encodeURIComponent(cluster.id)}`) : undefined
              }
            />

            {cluster ? (
              <div className="rounded-[1.75rem] border border-white/8 bg-night/70 p-5">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Linked error path</p>
                    <p className="mt-2 text-sm text-text-secondary">
                      Preview of the repair route that led to this note.
                    </p>
                  </div>
                  <Button variant="secondary" onClick={() => navigate(`/errors/${encodeURIComponent(cluster.id)}`)}>
                    Open full error page
                  </Button>
                </div>
                <div className="mt-4">
                  <ErrorChainMap steps={cluster.steps} variant="compact" />
                </div>
              </div>
            ) : null}
          </section>

          <aside className="min-w-0 space-y-6">
            <IssueRecommendations items={recommendations} />
            <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
              <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Everything captured</p>
              <div className="mt-4 space-y-3 text-sm text-text-secondary">
                <p className="break-words">Model used for note: <span className="text-white break-all">{note.model}</span></p>
                <p className="break-words">Velocity: <span className="text-white">{note.status}</span></p>
                <p className="break-words">Environment: <span className="text-white break-all">{note.environment.languageVersion}</span></p>
                <p className="break-words">Libraries: <span className="text-white break-all">{note.environment.libraries.join(', ') || 'Unknown'}</span></p>
                <p className="break-words">Linked cluster: <span className="text-white break-all">{cluster ? cluster.id : 'None'}</span></p>
                <p className="break-words">Trajectory: <span className="text-white break-all">{note.trajectoryId ?? 'Unknown'}</span></p>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
