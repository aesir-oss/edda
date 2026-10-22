import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Library } from './Library'
import { pages, sources } from './fixtures'
import { mockApi, renderAt } from './test-utils'

afterEach(() => vi.unstubAllGlobals())

test('lists every page with its type, status and description', async () => {
  mockApi({ '/api/pages': pages, '/api/sources': sources })
  renderAt('/', <Library />)
  expect(await screen.findByText('3 pages')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Attention Is All You Need' })).toHaveAttribute('href', '/page/papers/aiayn.md')
  expect(screen.getByText('How weights work.')).toBeInTheDocument()
  expect(screen.getByText('draft')).toBeInTheDocument()
})

test('filters by tag', async () => {
  mockApi({ '/api/pages': pages, '/api/sources': sources })
  renderAt('/?tag=search', <Library />)
  expect(await screen.findByText('1 page')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Filter by tag'), { target: { value: '' } })
  expect(await screen.findByText('3 pages')).toBeInTheDocument()
  expect(within(screen.getByLabelText('Filter by tag')).getAllByRole('option').map((o) => o.textContent)).toEqual([
    'All tags',
    'ml',
    'search',
  ])
})

test('searches and shows highlighted snippets', async () => {
  const fetchMock = mockApi({
    '/api/pages': pages,
    '/api/search': (url: URL) =>
      url.searchParams.get('q') === 'zebra'
        ? []
        : [
            { id: 'attention.md', title: 'Attention mechanism', description: 'd', score: 2, snippet: '<b>Attention</b> computes' },
            { id: 'bm25.md', title: 'BM25', description: 'Ranking.', score: 1, snippet: '' },
          ],
  })
  renderAt('/', <Library />)
  fireEvent.change(screen.getByLabelText('Search pages'), { target: { value: 'attention' } })
  const results = await screen.findByRole('list', { name: 'Search results' })
  expect(within(results).getAllByRole('link').map((a) => a.textContent)).toEqual(['Attention mechanism', 'BM25'])
  expect(results.querySelector('b')?.textContent).toBe('Attention')
  expect(within(results).getByText('Ranking.')).toBeInTheDocument()
  expect(String(fetchMock.mock.calls.at(-1)?.[0])).toBe('/api/search?q=attention&k=20')

  fireEvent.change(screen.getByLabelText('Search pages'), { target: { value: 'zebra' } })
  expect(await screen.findByText('No pages match.')).toBeInTheDocument()
})

test('shows errors from the API', async () => {
  mockApi({ '/api/pages': { status: 503, detail: 'no vault configured' }, '/api/search': { status: 500, detail: 42 } })
  renderAt('/', <Library />)
  expect(await screen.findByText('no vault configured')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Search pages'), { target: { value: 'x' } })
  expect(await screen.findByText('Error')).toBeInTheDocument()
})

test('filters by the source a page cites', async () => {
  mockApi({ '/api/pages': pages, '/api/sources': sources })
  renderAt('/', <Library />)
  const select = screen.getByLabelText('Filter by source')
  await within(select).findByRole('option', { name: 'Reading notes' })
  expect(within(select).getByRole('option', { name: 'd4d4516d' })).toBeInTheDocument()
  fireEvent.change(select, { target: { value: '887e1bdc' } })
  expect(await screen.findByText('1 page')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Attention mechanism' })).toBeInTheDocument()
})

test('filters drafts and stale pages, and marks stale ones', async () => {
  mockApi({ '/api/pages': pages, '/api/sources': sources })
  renderAt('/?status=stale', <Library />)
  expect(await screen.findByText('1 page')).toBeInTheDocument()
  expect(screen.getByText('stale')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'draft' } })
  expect(screen.getByRole('link', { name: 'BM25' })).toBeInTheDocument()
  expect(screen.getByText('1 page')).toBeInTheDocument()
})
