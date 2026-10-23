import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { AddSource } from './AddSource'
import { mockApi, renderAt } from './test-utils'

afterEach(() => vi.unstubAllGlobals())

const file = (name: string) => new File(['# Notes\n'], name, { type: 'text/markdown' })

test('uploads the chosen file and says what happened', async () => {
  let added = true
  const fetchMock = mockApi({
    '/api/sources/markdown': () => ({ source: { hash: 'ab'.repeat(32), title: added ? 'Notes' : null }, added }),
  })
  renderAt('/', <AddSource />)
  const input = screen.getByLabelText('Add a file')
  fireEvent.change(input, { target: { files: [file('notes.md')] } })
  expect(await screen.findByText('Added Notes')).toBeInTheDocument()
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe('/api/sources/markdown?filename=notes.md')
  expect(init.method).toBe('POST')

  added = false
  fireEvent.change(input, { target: { files: [file('again.md')] } })
  expect(await screen.findByText('Already stored')).toBeInTheDocument()
  fireEvent.change(input, { target: { files: [] } })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('offers to ingest what it added', async () => {
  const fetchMock = mockApi({
    '/api/sources/markdown': { source: { hash: 'ab'.repeat(32), title: 'Notes' }, added: true },
    '/api/jobs/ingest': { id: 'run_0001', job: 'ingest', target: 'ab'.repeat(32), state: 'queued', queued: 't' },
  })
  renderAt('/', <AddSource />)
  fireEvent.change(screen.getByLabelText('Add a file'), { target: { files: [file('notes.md')] } })
  fireEvent.click(await screen.findByRole('button', { name: 'Ingest' }))
  expect(await screen.findByRole('link', { name: 'Review' })).toHaveAttribute('href', '/review')
  const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
  expect(url).toBe('/api/jobs/ingest')
  expect(init.body).toBe(JSON.stringify({ source: 'ab'.repeat(32) }))

  mockApi({
    '/api/sources/markdown': { source: { hash: 'cd'.repeat(32), title: 'More' }, added: true },
    '/api/jobs/ingest': { status: 400, detail: 'no model' },
  })
  fireEvent.change(screen.getByLabelText('Add a file'), { target: { files: [file('more.md')] } })
  expect(await screen.findByText('Added More')).toBeInTheDocument()
})

test('names untitled sources by hash and shows errors', async () => {
  mockApi({ '/api/sources/markdown': () => ({ source: { hash: 'cd'.repeat(32), title: null }, added: true }) })
  renderAt('/', <AddSource />)
  fireEvent.change(screen.getByLabelText('Add a file'), { target: { files: [file('x.md')] } })
  expect(await screen.findByText(`Added ${'cd'.repeat(6)}`)).toBeInTheDocument()

  mockApi({ '/api/sources/markdown': { status: 400, detail: 'the file is empty' } })
  fireEvent.change(screen.getByLabelText('Add a file'), { target: { files: [file('y.md')] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('the file is empty')
})

test('adds a web page by address', async () => {
  const fetchMock = mockApi({
    '/api/sources/web': { source: { hash: 'ef'.repeat(32), title: 'Okapi BM25' }, added: true },
  })
  renderAt('/', <AddSource />)
  const button = screen.getByRole('button', { name: 'Add web page' })
  expect(button).toBeDisabled()
  const address = screen.getByLabelText('Web page address')
  fireEvent.change(address, { target: { value: ' https://en.wikipedia.org/wiki/Okapi_BM25 ' } })
  fireEvent.click(button)
  expect(await screen.findByText('Added Okapi BM25')).toBeInTheDocument()
  expect(address).toHaveValue('')
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe('/api/sources/web')
  expect(JSON.parse(String(init.body))).toEqual({ url: 'https://en.wikipedia.org/wiki/Okapi_BM25' })
  expect(screen.getByRole('button', { name: 'Ingest' })).toBeInTheDocument()

  mockApi({ '/api/sources/web': { status: 403, detail: 'example.com is not in [network] allow in policy.toml' } })
  fireEvent.change(address, { target: { value: 'https://example.com/' } })
  fireEvent.submit(address.closest('form') as HTMLFormElement)
  expect(await screen.findByRole('alert')).toHaveTextContent('not in [network] allow')
  expect(address).toHaveValue('https://example.com/')
})

test('sends documents to the document importer and offers to ingest the Markdown made from them', async () => {
  const derived = { source: { hash: 'md'.repeat(32), title: 'Lantern protocol', derived_from: 'pd'.repeat(32) }, added: true }
  const fetchMock = mockApi({
    '/api/sources/document': { original: { source: { hash: 'pd'.repeat(32), title: 'Lantern protocol' }, added: true }, derived },
    '/api/jobs/ingest': { id: 'run_0001', job: 'ingest', target: 'md'.repeat(32), state: 'queued', queued: 't' },
  })
  renderAt('/', <AddSource />)
  const pdf = new File(['%PDF-1.7'], 'Lantern.PDF', { type: 'application/pdf' })
  fireEvent.change(screen.getByLabelText('Add a file'), { target: { files: [pdf] } })
  expect(await screen.findByText('Added Lantern protocol')).toBeInTheDocument()
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe('/api/sources/document?filename=Lantern.PDF')
  expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/octet-stream')
  fireEvent.click(screen.getByRole('button', { name: 'Ingest' }))
  await screen.findByRole('link', { name: 'Review' })
  expect(JSON.parse(String((fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1].body))).toEqual({
    source: 'md'.repeat(32),
  })
})
