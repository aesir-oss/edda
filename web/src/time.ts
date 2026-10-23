/** "45s", "2m 10s", "1h 3m": the time from `start` to `now`, both in milliseconds. */
export function elapsed(start: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - start) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
