import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import { invalidateFor, useVaultEvents } from './events'

afterEach(() => vi.unstubAllGlobals())

class FakeEventSource {
  static last: FakeEventSource | undefined
  readonly listeners = new Map<string, () => void>()
  closed = false
  readonly url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener(event: string, listener: () => void) {
    this.listeners.set(event, listener)
  }
  removeEventListener(event: string) {
    this.listeners.delete(event)
  }
  close() {
    this.closed = true
  }
}

test('each event marks the queries it affects stale', () => {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  invalidateFor(client, 'source')
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['sources'] })
  invalidateFor(client, 'unknown')
  expect(invalidate).toHaveBeenCalledTimes(1)
})

test('listens to /api/events while mounted', () => {
  vi.stubGlobal('EventSource', FakeEventSource)
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { unmount } = renderHook(() => useVaultEvents(), { wrapper })
  const source = FakeEventSource.last
  expect(source?.url).toBe('/api/events')
  source?.listeners.get('job')?.()
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jobs'] })
  unmount()
  expect(source?.closed).toBe(true)
  expect(source?.listeners.size).toBe(0)
})

test('does nothing where EventSource is missing', () => {
  vi.stubGlobal('EventSource', undefined)
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
  )
  expect(() => renderHook(() => useVaultEvents(), { wrapper })).not.toThrow()
})
