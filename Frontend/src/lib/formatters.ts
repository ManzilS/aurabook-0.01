export function formatRelativeTime(input: string): string {
  const date = new Date(input)
  const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

  const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 60 * 60 * 24 * 365],
    ['month', 60 * 60 * 24 * 30],
    ['day', 60 * 60 * 24],
    ['hour', 60 * 60],
    ['minute', 60],
    ['second', 1],
  ]

  for (const [unit, secondsInUnit] of ranges) {
    if (Math.abs(diffSeconds) >= secondsInUnit || unit === 'second') {
      return formatter.format(Math.round(diffSeconds / secondsInUnit), unit)
    }
  }

  return formatter.format(0, 'second')
}

export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat('en', { notation: 'compact' }).format(value)
}
