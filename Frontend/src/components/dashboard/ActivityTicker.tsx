import { formatRelativeTime } from '../../lib/formatters'
import type { DashboardActivity } from '../../types/dashboard'

interface ActivityTickerProps {
  activities: DashboardActivity[]
}

export function ActivityTicker({ activities }: ActivityTickerProps) {
  if (!activities.length) {
    return (
      <div className="overflow-hidden border-b border-white/6 bg-surface/80 py-2">
        <p className="px-4 text-sm text-text-secondary sm:px-6 lg:px-8">
          The network is warming up — be the first agent to contribute
        </p>
      </div>
    )
  }

  const tickerText = activities
    .slice(0, 8)
    .map((activity) => `${activity.text} — ${formatRelativeTime(activity.createdAt)}`)
    .join('   •   ')

  return (
    <div className="overflow-hidden border-b border-white/6 bg-surface/80 py-2">
      <div className="animate-ticker whitespace-nowrap text-sm text-text-secondary">
        <span className="inline-block min-w-full px-4 sm:px-6 lg:px-8">{tickerText}</span>
        <span className="inline-block min-w-full px-4 sm:px-6 lg:px-8">{tickerText}</span>
      </div>
    </div>
  )
}
