import type { components } from './schema'

type Schemas = components['schemas']
export type PageSummary = Schemas['PageSummary']
export type Page = Schemas['Page']
export type SearchHit = Schemas['SearchHit']
export type Links = Schemas['Links']
export type VaultInfo = Schemas['VaultInfo']
export type Citation = Schemas['Citation']

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const url = query.size ? `${path}?${query}` : path
  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown }
    const detail = typeof body.detail === 'string' ? body.detail : response.statusText
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

/** Page ids contain slashes, which stay as path separators; each segment is escaped. */
const pagePath = (id: string) => id.split('/').map(encodeURIComponent).join('/')

export const api = {
  vault: () => get<VaultInfo>('/api/vault'),
  pages: (filter: { tag?: string; type?: string } = {}) => get<PageSummary[]>('/api/pages', filter),
  page: (id: string) => get<Page>(`/api/pages/${pagePath(id)}`),
  links: (id: string) => get<Links>(`/api/links/${pagePath(id)}`),
  search: (q: string, k = 20) => get<SearchHit[]>('/api/search', { q, k }),
}
