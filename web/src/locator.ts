/** "page 5", "p. 7", "pages 3-4": the first page a locator names, if it names one. */
export function pageOf(locator: string): number | undefined {
  const match = /\b(?:pages?|pp?\.)\s*(\d+)/i.exec(locator)
  return match ? Number(match[1]) : undefined
}

/** "lines 3-5", "line 7", "Lines 3 to 6": the line range a locator names, if it names one. */
export function lineRange(locator: string): { start: number; end: number } | undefined {
  const match = /\blines?\s+(\d+)(?:\s*(?:-|–|to)\s*(\d+))?/i.exec(locator)
  if (!match) return undefined
  const start = Number(match[1])
  return { start, end: match[2] ? Number(match[2]) : start }
}
