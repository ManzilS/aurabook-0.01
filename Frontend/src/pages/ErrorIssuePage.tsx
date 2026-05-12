import { useEffect, useMemo } from 'react'
import { ArrowLeft, BookOpenText } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button } from '../components/common/Button'
import { SkeletonCard } from '../components/common/SkeletonCard'
import { ErrorClusterDetailPanel } from '../components/errors/ErrorClusterDetailPanel'
import { IssueRecommendations } from '../components/issues/IssueRecommendations'
import { recommendationsForErrorCluster } from '../lib/issueRecommendations'
import { formatCompactNumber } from '../lib/formatters'
import { useDashboardStore } from '../store/dashboardStore'

export function ErrorIssuePage() {
  const navigate = useNavigate()
  const { clusterId } = useParams<{ clusterId: string }>()
  const {
    loading,
    notes,
    errorClusters,
    initialize,
    teardown,
    downloadErrorCluster,
  } = useDashboardStore()

  useEffect(() => {
    void initialize()
    return () => teardown()
  }, [initialize, teardown])

  const cluster = useMemo(
    () => errorClusters.find((candidate) => candidate.id === clusterId) ?? null,
    [clusterId, errorClusters],
  )
  const note = useMemo(
    () =>
      cluster?.noteId == null
        ? null
        : notes.find((candidate) => candidate.id === cluster.noteId) ?? null,
    [cluster, notes],
  )
  const recommendations = useMemo(
    () => (cluster ? recommendationsForErrorCluster(cluster, note) : []),
    [cluster, note],
  )

  if (loading && !cluster) {
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

  if (!cluster) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12 text-center sm:px-6 lg:px-8">
        <p className="text-xs uppercase tracking-[0.35em] text-accent">AuraBook Error</p>
        <h1 className="mt-4 text-4xl font-semibold text-white">Error cluster not found</h1>
        <p className="mt-4 text-text-secondary">
          The requested error cluster does not exist in the current dashboard snapshot.
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
            <p className="text-xs uppercase tracking-[0.35em] text-accent">AuraBook Error</p>
            <h1 className="mt-3 break-words text-4xl font-semibold text-white">{cluster.problemTag}</h1>
            <p className="mt-3 max-w-3xl text-text-secondary">
              Full error record, branch map, failure chain, linked note, and the richest issue-level context available.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button variant="secondary" onClick={() => navigate('/dashboard')}>
              <ArrowLeft className="mr-2" size={16} />
              Back to dashboard
            </Button>
            {note ? (
              <Button onClick={() => navigate(`/notes/${encodeURIComponent(note.id)}`)}>
                <BookOpenText className="mr-2" size={16} />
                Open note page
              </Button>
            ) : null}
          </div>
        </div>

        <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <section className="min-w-0 space-y-6">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Failures</p>
                <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(cluster.failureCount)}</p>
              </div>
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Attempts</p>
                <p className="mt-2 text-3xl font-semibold text-white">{formatCompactNumber(cluster.attemptCount)}</p>
              </div>
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Models tried</p>
                <p className="mt-2 text-3xl font-semibold text-white">
                  {formatCompactNumber(cluster.attemptedModels.length)}
                </p>
              </div>
              <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
                <p className="text-sm text-text-secondary">Follow-on errors</p>
                <p className="mt-2 text-3xl font-semibold text-white">
                  {formatCompactNumber(cluster.cascadeErrorCount)}
                </p>
              </div>
            </div>

            <ErrorClusterDetailPanel
              cluster={cluster}
              onDownload={() => downloadErrorCluster(cluster.id)}
            />
          </section>

          <aside className="min-w-0 space-y-6">
            <IssueRecommendations items={recommendations} />
            <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5">
              <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">Everything captured</p>
              <div className="mt-4 space-y-3 text-sm text-text-secondary">
                <p className="break-words">Status: <span className="text-white">{cluster.unresolved ? 'Unresolved' : 'Resolved'}</span></p>
                <p className="break-words">Language: <span className="text-white">{cluster.language}</span></p>
                <p className="break-words">Related trajectories: <span className="text-white">{cluster.relatedTrajectoryIds.length}</span></p>
                <p className="break-words">Note link: <span className="text-white break-all">{note ? note.id : 'None'}</span></p>
                <p className="break-words">Primary trajectory: <span className="text-white break-all">{cluster.trajectoryId ?? 'Unknown'}</span></p>
                <p className="break-words">Attempted models: <span className="text-white break-all">{cluster.attemptedModels.join(', ') || 'None captured'}</span></p>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
