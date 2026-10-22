/** "lines 3-5", "line 7", "Lines 3 to 6": the line range a locator names, if it names one. */
export function lineRange(locator: string): { start: number; end: number } | undefined {
  const match = /\blines?\s+(\d+)(?:\s*(?:-|–|to)\s*(\d+))?/i.exec(locator)
  if (!match) return undefined
  const start = Number(match[1])
  return { start, end: match[2] ? Number(match[2]) : start }
}
