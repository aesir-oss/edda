import { afterEach, expect, test, vi } from 'vitest'
import { api, ApiError } from './client'

afterEach(() => vi.unstubAllGlobals())

test('escapes page id segments but keeps the slashes', async () => {
  const fetchMock = vi.fn(async () => new Response('{}'))
  vi.stubGlobal('fetch', fetchMock)
  await api.page('papers/a b.md')
  await api.links('x.md')
  await api.pages({ tag: 'ml', type: '' })
  expect(fetchMock.mock.calls.map((c) => String((c as unknown[])[0]))).toEqual([
    '/api/pages/papers/a%20b.md',
    '/api/links/x.md',
    '/api/pages?tag=ml',
  ])
})

test('falls back to the status text when an error has no JSON body', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('oops', { status: 502, statusText: 'Bad Gateway' })))
  const error = await api.vault().catch((e: unknown) => e)
  expect(error).toBeInstanceOf(ApiError)
  expect(error).toMatchObject({ status: 502, message: 'Bad Gateway' })
})
