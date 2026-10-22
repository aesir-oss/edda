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
  const input = screen.getByLabelText('Add Markdown source')
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

test('names untitled sources by hash and shows errors', async () => {
  mockApi({ '/api/sources/markdown': () => ({ source: { hash: 'cd'.repeat(32), title: null }, added: true }) })
  renderAt('/', <AddSource />)
  fireEvent.change(screen.getByLabelText('Add Markdown source'), { target: { files: [file('x.md')] } })
  expect(await screen.findByText(`Added ${'cd'.repeat(6)}`)).toBeInTheDocument()

  mockApi({ '/api/sources/markdown': { status: 400, detail: 'the file is empty' } })
  fireEvent.change(screen.getByLabelText('Add Markdown source'), { target: { files: [file('y.md')] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('the file is empty')
})
