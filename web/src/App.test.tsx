import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App, { Shell } from './App'
import { pages } from './fixtures'
import { mockApi, renderAt } from './test-utils'

afterEach(() => vi.unstubAllGlobals())

test('shows the vault title and the library at the root', async () => {
  mockApi({ '/api/vault': { title: 'Example vault', okf_version: '0.2', pages: 3 }, '/api/pages': pages })
  render(<App />)
  expect(await screen.findByText('Example vault')).toBeInTheDocument()
  expect(await screen.findByText('3 pages')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Log' })).toHaveAttribute('href', '/page/log.md')
})

test('unknown routes link back to the library', async () => {
  mockApi({ '/api/vault': { title: 'V', okf_version: '0.2', pages: 0 } })
  renderAt('/nowhere', <Shell />)
  expect(screen.getByRole('link', { name: 'Back to the library' })).toHaveAttribute('href', '/')
})
