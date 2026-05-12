import type { IssueRecommendation } from '../../lib/issueRecommendations'

interface IssueRecommendationsProps {
  title?: string
  items: IssueRecommendation[]
}

export function IssueRecommendations({
  title = 'Recommended next moves',
  items,
}: IssueRecommendationsProps) {
  if (!items.length) {
    return null
  }

  return (
    <div className="rounded-[1.75rem] border border-white/8 bg-white/4 p-5 overflow-hidden">
      <p className="text-sm uppercase tracking-[0.3em] text-text-secondary">{title}</p>
      <div className="mt-4 space-y-3">
        {items.map((item) => (
          <div key={item.title} className="rounded-2xl border border-white/8 bg-night/70 p-4">
            <p className="break-words text-sm font-semibold text-white">{item.title}</p>
            <p className="mt-2 break-words text-sm leading-7 text-text-secondary">{item.detail}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
