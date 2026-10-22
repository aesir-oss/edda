import { fireEvent, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { attention } from './fixtures'
import { PageView } from './PageView'
import { mockApi, renderAt } from './test-utils'

afterEach(() => vi.unstubAllGlobals())

const routes = (
  <Routes>
    <Route path="/page/*" element={<PageView />} />
  </Routes>
)

test('renders the page with citations, sources and backlinks', async () => {
  mockApi({
    '/api/pages/attention.md': attention,
    '/api/links/attention.md': { outgoing: [], backlinks: ['papers/aiayn.md'] },
  })
  renderAt('/page/attention.md', routes)
  expect(await screen.findByRole('heading', { name: 'Attention mechanism' })).toBeInTheDocument()
  expect(screen.getByText(/revision 2/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '1' })).toHaveAttribute('title', 'The paper · Section 3.2.')
  expect(screen.getByRole('link', { name: '2' })).not.toHaveAttribute('title')
  expect(screen.getByRole('link', { name: 'The paper' })).toHaveAttribute('href', 'https://arxiv.org/abs/1706.03762')
  expect(screen.getByTitle('raw:887e')).toHaveTextContent('raw:887e')
  expect(screen.getByText('source notes is never cited')).toBeInTheDocument()
  expect(await screen.findByRole('link', { name: 'papers/aiayn.md' })).toHaveAttribute('href', '/page/papers/aiayn.md')
})

test('follows page links inside the app and leaves others alone', async () => {
  const bm25 = { ...attention, id: 'bm25.md', title: 'BM25', rev: null, frontmatter: {}, issues: [], citations: [], html: '<h1>BM25</h1>' }
  mockApi({
    '/api/pages/attention.md': attention,
    '/api/pages/bm25.md': bm25,
    '/api/links/attention.md': { outgoing: [], backlinks: [] },
    '/api/links/bm25.md': { outgoing: [], backlinks: [] },
  })
  renderAt('/page/attention.md', routes)
  await screen.findByRole('heading', { name: 'Attention mechanism' })
  expect(await screen.findByText('No pages link here.')).toBeInTheDocument()

  const external = screen.getByRole('link', { name: 'the web' })
  expect(fireEvent.click(external)).toBe(true)
  expect(fireEvent.click(screen.getByRole('link', { name: 'BM25' }), { metaKey: true })).toBe(true)
  expect(fireEvent.click(screen.getByRole('link', { name: 'BM25' }))).toBe(false)
  expect(await screen.findByRole('heading', { name: 'BM25' })).toBeInTheDocument()
  expect(screen.getByText('No sources.')).toBeInTheDocument()
})

test('shows a not-found notice', async () => {
  mockApi({ '/api/pages/gone.md': { status: 404, detail: 'no page gone.md' }, '/api/links/gone.md': { status: 404, detail: 'x' } })
  renderAt('/page/gone.md', routes)
  expect(await screen.findByText('Not found')).toBeInTheDocument()
  expect(screen.getByText('no page gone.md')).toBeInTheDocument()
})
