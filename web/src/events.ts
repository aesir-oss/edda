import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

/** Which cached queries each server-sent event makes stale. */
const STALE: Record<string, string[][]> = {
  proposal: [['proposals'], ['proposal'], ['pages'], ['page'], ['links'], ['search']],
  page: [['pages'], ['page'], ['links'], ['search']],
  source: [['sources']],
  job: [['jobs']],
}

export function invalidateFor(queryClient: QueryClient, event: string) {
  for (const queryKey of STALE[event] ?? []) void queryClient.invalidateQueries({ queryKey })
}

/** Keeps the cache current while the shell is open: the API pushes what changed and the affected queries refetch. */
export function useVaultEvents() {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (typeof EventSource === 'undefined') return
    const source = new EventSource('/api/events')
    const listeners = Object.keys(STALE).map((event) => {
      const listener = () => invalidateFor(queryClient, event)
      source.addEventListener(event, listener)
      return [event, listener] as const
    })
    return () => {
      for (const [event, listener] of listeners) source.removeEventListener(event, listener)
      source.close()
    }
  }, [queryClient])
}
