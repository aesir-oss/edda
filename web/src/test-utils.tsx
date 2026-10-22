import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'
import { makeQueryClient } from './queryClient'

type Routes = Record<string, unknown | ((url: URL) => unknown)>

/** Replaces fetch with one that answers each path from `routes`; a value of `{ status, detail }` is an error. */
export function mockApi(routes: Routes) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://edda.test')
    const entry = routes[url.pathname]
    const body = typeof entry === 'function' ? (entry as (url: URL) => unknown)(url) : entry
    if (body === undefined) return new Response(JSON.stringify({ detail: 'no route' }), { status: 404 })
    if (body && typeof body === 'object' && 'status' in body && 'detail' in body) {
      const { status, detail } = body as { status: number; detail: unknown }
      return new Response(JSON.stringify({ detail }), { status, statusText: 'Error' })
    }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

export function renderAt(path: string, ui: ReactNode) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}
