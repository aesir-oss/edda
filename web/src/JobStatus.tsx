import { useEffect, useState } from 'react'
import type { JobRun } from './api/client'
import { elapsed } from './time'

/** Ticks once a second while `on`, so elapsed times stay current. */
function useNow(on: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [on])
  return now
}

/** Jobs since the API started: what is running and how far along, what failed, and what stopped short. */
export function JobStatus({ jobs }: { jobs: JobRun[] }) {
  const active = jobs.filter((j) => j.state === 'queued' || j.state === 'running')
  const now = useNow(active.length > 0)
  const name = (j: JobRun) => `${j.job} ${j.target.slice(0, 8)}`
  return (
    <>
      {active.map((j) => (
        <p key={j.id} role="status" className="muted job">
          {name(j)}
          {j.state === 'queued' ? ' · queued' : ' · running'}
          {j.turn != null && j.max_turns != null && ` · turn ${j.turn} of ${j.max_turns}`}
          {j.started && ` · ${elapsed(Date.parse(j.started), now)}`}
        </p>
      ))}
      {jobs
        .filter((j) => j.state === 'failed')
        .map((j) => (
          <p key={j.id} role="alert" className="error">
            {name(j)} failed: {j.error}
          </p>
        ))}
      {jobs
        .filter((j) => j.state === 'done' && j.note)
        .map((j) => (
          <p key={j.id} className="job job-note">
            {name(j)} {j.note}
          </p>
        ))}
    </>
  )
}
