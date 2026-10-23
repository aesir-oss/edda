import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { Proposal } from './api/client'
import { editProposal, flagProposal, pageProposal } from './fixtures'
import { Diff, Review } from './Review'
import { mockApi, renderAt } from './test-utils'

afterEach(() => vi.unstubAllGlobals())

const decision = { proposal: editProposal, entry: {}, revised_from: null }

function routes(extra: Record<string, unknown> = {}) {
  return mockApi({
    '/api/proposals': [editProposal, pageProposal, flagProposal],
    '/api/proposals/prop_000001': editProposal,
    '/api/proposals/prop_000002': pageProposal,
    '/api/proposals/prop_000003': flagProposal,
    '/api/sources/ab12': (url: URL) => ({ hash: 'ab12', text: url.searchParams.get('start') ? '3| Zebras graze.' : 'all of it' }),
    '/api/jobs': [],
    ...extra,
  })
}

function renderReview(path = '/review') {
  return renderAt(
    path,
    <Routes>
      <Route path="/review" element={<Review />} />
      <Route path="/review/:id" element={<Review />} />
    </Routes>,
  )
}

const call = (fetchMock: ReturnType<typeof mockApi>, path: string) =>
  fetchMock.mock.calls.find(([url]) => String(url) === path) as unknown as [string, RequestInit] | undefined

test('lists pending proposals and shows the first with its diff and cited source text', async () => {
  const fetchMock = routes()
  renderReview()
  expect(await screen.findByText('3 pending')).toBeInTheDocument()
  const queue = screen.getByRole('navigation', { name: 'Pending proposals' })
  expect(within(queue).getAllByRole('link').map((a) => a.textContent)).toEqual(['bm25.md', 'zebra.md', 'bm25.md'])
  expect(within(queue).getByText('blocked')).toBeInTheDocument()
  expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/proposals?status=pending')

  const article = await screen.findByRole('article', { name: 'Proposal prop_000001' })
  expect(within(article).getByText('adds zebras')).toBeInTheDocument()
  expect(within(article).getByText('+orphan. Zebras graze.')).toHaveClass('diff-add')
  expect(within(article).getByText('-orphan.')).toHaveClass('diff-del')
  expect(await within(article).findByText('3| Zebras graze.')).toBeInTheDocument()
  expect(within(article).getByText('The locator names no lines; show the whole source')).toBeInTheDocument()
  expect(call(fetchMock, '/api/sources/ab12?start=3&end=4')).toBeDefined()
})

test('accepts as proposed, or after editing the text', async () => {
  const fetchMock = routes({ '/api/proposals/prop_000001/accept': decision })
  renderReview('/review/prop_000001')
  const article = await screen.findByRole('article', { name: 'Proposal prop_000001' })
  fireEvent.click(within(article).getByRole('button', { name: 'Accept' }))
  await waitFor(() => expect(call(fetchMock, '/api/proposals/prop_000001/accept')).toBeDefined())
  expect(call(fetchMock, '/api/proposals/prop_000001/accept')?.[1].body).toBe('{}')

  fetchMock.mockClear()
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
  fireEvent.change(screen.getByLabelText('Edit 1'), { target: { value: 'orphan. Wild zebras graze.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Accept my version' }))
  await waitFor(() => expect(call(fetchMock, '/api/proposals/prop_000001/accept')).toBeDefined())
  expect(JSON.parse(String(call(fetchMock, '/api/proposals/prop_000001/accept')?.[1].body))).toEqual({
    edits: [{ old: 'orphan.', new: 'orphan. Wild zebras graze.' }],
  })
})

test('discarding an edit goes back to the diff', async () => {
  routes()
  renderReview('/review/prop_000001')
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
  fireEvent.click(screen.getByRole('button', { name: 'Discard my changes' }))
  expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument()
  expect(screen.getByLabelText('Changes')).toBeInTheDocument()
})

test('revises a new page field by field', async () => {
  const fetchMock = routes({ '/api/proposals/prop_000002/accept': decision })
  renderReview('/review/prop_000002')
  const article = await screen.findByRole('article', { name: 'Proposal prop_000002' })
  expect(within(article).getByLabelText('Issues')).toHaveTextContent('cites no source')
  fireEvent.click(within(article).getByRole('button', { name: 'Edit' }))
  fireEvent.change(within(article).getByLabelText('Title'), { target: { value: 'Zebras' } })
  fireEvent.change(within(article).getByLabelText('Description'), { target: { value: 'Striped horses.' } })
  fireEvent.change(within(article).getByLabelText('Body'), { target: { value: 'Zebras, as I know them.\n' } })
  fireEvent.click(within(article).getByRole('button', { name: 'Accept my version' }))
  await waitFor(() => expect(call(fetchMock, '/api/proposals/prop_000002/accept')).toBeDefined())
  expect(JSON.parse(String(call(fetchMock, '/api/proposals/prop_000002/accept')?.[1].body))).toEqual({
    page: { title: 'Zebras', description: 'Striped horses.', body: 'Zebras, as I know them.\n' },
  })
})

test('rejects with a reason and shows refusals from the kernel', async () => {
  const fetchMock = routes({
    '/api/proposals/prop_000003/reject': decision,
    '/api/proposals/prop_000003/accept': { status: 409, detail: 'page bm25.md is at revision 2, not 1' },
  })
  renderReview('/review/prop_000003')
  const article = await screen.findByRole('article', { name: 'Proposal prop_000003' })
  expect(within(article).getByText('No example.')).toBeInTheDocument()
  expect(within(article).queryByRole('button', { name: 'Edit' })).toBeNull()
  fireEvent.click(within(article).getByRole('button', { name: 'Accept' }))
  expect(await within(article).findByRole('alert')).toHaveTextContent('revision 2, not 1')

  const reject = within(article).getByRole('button', { name: 'Reject' })
  expect(reject).toBeDisabled()
  fireEvent.change(within(article).getByLabelText('Reason for rejecting'), { target: { value: 'covered elsewhere' } })
  fireEvent.click(reject)
  await waitFor(() => expect(call(fetchMock, '/api/proposals/prop_000003/reject')).toBeDefined())
  expect(call(fetchMock, '/api/proposals/prop_000003/reject')?.[1].body).toBe('{"reason":"covered elsewhere"}')
})

test('shows running and failed jobs, an empty queue, and errors', async () => {
  mockApi({
    '/api/proposals': [],
    '/api/jobs': [
      { id: 'run_0001', job: 'ingest', target: 'ab12cd34ef', state: 'running', queued: 't' },
      { id: 'run_0002', job: 'ingest', target: 'ff00ff00aa', state: 'failed', queued: 't', error: 'model went away' },
    ],
  })
  renderReview()
  expect(await screen.findByText('Nothing to review.')).toBeInTheDocument()
  expect(await screen.findByText(/ingest ab12cd34 · running/)).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('ingest ff00ff00 failed: model went away')

  mockApi({ '/api/proposals': { status: 503, detail: 'no vault configured' } })
  renderReview()
  expect(await screen.findByText('no vault configured')).toBeInTheDocument()
})

test('a proposal that cannot be read says so', async () => {
  routes({ '/api/proposals/prop_000009': { status: 404, detail: 'no proposal prop_000009' } })
  renderReview('/review/prop_000009')
  expect(await screen.findByText('no proposal prop_000009')).toBeInTheDocument()
})

test('a cited source that cannot be read says so', async () => {
  routes({ '/api/sources/ab12': { status: 404, detail: 'no source ab12' } })
  renderReview('/review/prop_000001')
  expect((await screen.findAllByText('no source ab12')).length).toBe(2)
})

test('an empty diff renders nothing', () => {
  const { container } = renderAt('/', <Diff text="" />)
  expect(container).toBeEmptyDOMElement()
})

const linked = (id: string, page: string, other: string): Proposal => ({
  ...pageProposal,
  id,
  change: { ...pageProposal.change, kind: 'new_page', page, title: page, description: 'd', body: `See /${other}.\n`, type: 'Concept' },
  preview: { diff: `+See /${other}.\n`, issues: [`link /${other} points to a page that does not exist`] },
})

test('offers to accept pages that link to each other together', async () => {
  const devin = linked('prop_000010', 'concepts/devin.md', 'concepts/deepwiki.md')
  const deepwiki = linked('prop_000011', 'concepts/deepwiki.md', 'concepts/devin.md')
  const fetchMock = mockApi({
    '/api/proposals': [devin, deepwiki],
    '/api/proposals/prop_000010': devin,
    '/api/proposals/prop_000011': deepwiki,
    '/api/jobs': [],
    '/api/proposals/accept-together': [decision, decision],
  })
  renderReview('/review/prop_000010')
  const article = await screen.findByRole('article', { name: 'Proposal prop_000010' })
  expect(within(article).getByText(/It links to concepts\/deepwiki\.md, which another proposal creates/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /together/ })).toBeNull()

  fireEvent.click(within(article).getByRole('button', { name: 'Select both' }))
  expect(screen.getByLabelText('Select concepts/devin.md')).toBeChecked()
  expect(screen.getByLabelText('Select concepts/deepwiki.md')).toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: 'Accept 2 together' }))
  await waitFor(() => expect(call(fetchMock, '/api/proposals/accept-together')).toBeDefined())
  expect(JSON.parse(String(call(fetchMock, '/api/proposals/accept-together')?.[1].body))).toEqual({
    ids: ['prop_000010', 'prop_000011'],
  })
  await waitFor(() => expect(screen.getByLabelText('Select concepts/devin.md')).not.toBeChecked())
})

test('selection by hand, and refusals of the whole set', async () => {
  const fetchMock = routes({
    '/api/proposals/accept-together': { status: 400, detail: 'prop_000002: added text cites no source: "Zebras."' },
  })
  renderReview('/review/prop_000001')
  await screen.findByRole('article', { name: 'Proposal prop_000001' })
  expect(screen.queryByText(/accept them together/)).toBeNull()
  fireEvent.click(screen.getByLabelText('Select zebra.md'))
  expect(screen.queryByRole('button', { name: /together/ })).toBeNull()
  const boxes = screen.getAllByLabelText('Select bm25.md')
  fireEvent.click(boxes[0] as HTMLElement)
  fireEvent.click(boxes[1] as HTMLElement)
  fireEvent.click(boxes[1] as HTMLElement)
  fireEvent.click(screen.getByRole('button', { name: 'Accept 2 together' }))
  expect(await screen.findByText(/cites no source: "Zebras\."/)).toBeInTheDocument()
  expect(JSON.parse(String(call(fetchMock, '/api/proposals/accept-together')?.[1].body))).toEqual({
    ids: ['prop_000002', 'prop_000001'],
  })
})
