import { expect, test } from 'vitest'
import { lineRange } from './locator'

test.each([
  ['lines 3-5', { start: 3, end: 5 }],
  ['Lines 3 to 6', { start: 3, end: 6 }],
  ['line 7', { start: 7, end: 7 }],
  ['p. 4, lines 10–12', { start: 10, end: 12 }],
  ['Section 3.2', undefined],
])('%s', (locator, expected) => {
  expect(lineRange(locator)).toEqual(expected)
})
