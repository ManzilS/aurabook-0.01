import { cn } from '../../lib/cn'
import { useAppStore } from '../../store/appStore'

const statusMap = {
  standby: { dot: 'bg-white/20', label: 'Dashboard idle' },
  connected: { dot: 'bg-success', label: 'Live updates' },
  connecting: { dot: 'bg-warning', label: 'Syncing' },
  polling: { dot: 'bg-accent', label: 'Live snapshot' },
  preview: { dot: 'bg-warning', label: 'Local preview' },
  disconnected: { dot: 'bg-white/20', label: 'Cloud offline' },
}

export function ConnectionStatus() {
  const status = useAppStore((state) => state.connectionStatus)
  const current = statusMap[status]

  return (
    <div
      className="hidden items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-text-secondary md:inline-flex"
      title="This indicates the browser's connection to the dashboard API, not your AI provider. Open Dashboard to connect; updates arrive by WebSocket or polling."
      tabIndex={0}
      aria-label={`${current.label}. Dashboard API connection. Open Dashboard to connect.`}
    >
      <span className={cn('h-2.5 w-2.5 rounded-full', current.dot)} />
      <span>{current.label}</span>
    </div>
  )
}
