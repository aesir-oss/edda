import { expect, test } from 'vitest'
import { elapsed } from './time'

test.each([
  [0, '0s'],
  [45_900, '45s'],
  [130_000, '2m 10s'],
  [3_780_000, '1h 3m'],
  [-5_000, '0s'],
])('%d ms is %s', (ms, text) => {
  expect(elapsed(1_000_000, 1_000_000 + ms)).toBe(text)
})
