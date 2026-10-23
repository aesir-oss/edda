import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { JobRun } from './api/client'
import { JobStatus } from './JobStatus'

afterEach(() => vi.useRealTimers())

const run = (fields: Partial<JobRun>): JobRun => ({
  id: 'run_0001',
  job: 'ingest',
  target: 'ab12cd34ef56',
  state: 'running',
  queued: '2026-10-22T10:00:00Z',
  proposals: [],
  ...fields,
})

test('shows the turn and a ticking elapsed time for a running job', () => {
  vi.useFakeTimers()
  vi.setSystemTime(Date.parse('2026-10-22T10:02:10Z'))
  render(<JobStatus jobs={[run({ started: '2026-10-22T10:00:00Z', turn: 7, max_turns: 32 })]} />)
  expect(screen.getByRole('status')).toHaveTextContent('ingest ab12cd34 · running · turn 7 of 32 · 2m 10s')
  act(() => vi.advanceTimersByTime(5000))
  expect(screen.getByRole('status')).toHaveTextContent('2m 15s')
})

test('shows queued jobs, failures and jobs that stopped short', () => {
  render(
    <JobStatus
      jobs={[
        run({ id: 'r1', state: 'queued' }),
        run({ id: 'r2', state: 'failed', error: 'is the model server running?' }),
        run({ id: 'r3', state: 'done', note: 'stopped at its limit of 32 model turns' }),
        run({ id: 'r4', state: 'done' }),
      ]}
    />,
  )
  expect(screen.getByRole('status')).toHaveTextContent('ingest ab12cd34 · queued')
  expect(screen.getByRole('alert')).toHaveTextContent('ingest ab12cd34 failed: is the model server running?')
  expect(screen.getByText('ingest ab12cd34 stopped at its limit of 32 model turns')).toBeInTheDocument()
  expect(screen.getAllByText(/ingest ab12cd34/)).toHaveLength(3)
})
