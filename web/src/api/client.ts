import type { components } from './schema'

type Schemas = components['schemas']
export type PageSummary = Schemas['PageSummary']
export type Page = Schemas['Page']
export type SearchHit = Schemas['SearchHit']
export type Links = Schemas['Links']
export type VaultInfo = Schemas['VaultInfo']
export type Citation = Schemas['Citation']
export type SourceSummary = Schemas['SourceSummary']
export type AddedSource = Schemas['AddedSource']
export type Source = Schemas['Source']
export type Proposal = Schemas['Proposal']
export type ProposalCitation = Schemas['ProposalCitation']
export type TextEdit = Schemas['TextEdit']
export type PageRevision = Schemas['PageRevision']
export type AcceptRequest = Schemas['AcceptRequest']
export type Decision = Schemas['Decision']
export type JobRun = Schemas['JobRun']

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Params = Record<string, string | number | undefined>

async function request<T>(path: string, params: Params = {}, init: RequestInit = {}): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const url = query.size ? `${path}?${query}` : path
  const response = await fetch(url, { ...init, headers: { Accept: 'application/json', ...init.headers } })
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown }
    const detail = typeof body.detail === 'string' ? body.detail : response.statusText
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(body),
  headers: { 'Content-Type': 'application/json' },
})

/** Page ids contain slashes, which stay as path separators; each segment is escaped. */
const pagePath = (id: string) => id.split('/').map(encodeURIComponent).join('/')

export const api = {
  vault: () => request<VaultInfo>('/api/vault'),
  pages: (filter: { tag?: string; type?: string } = {}) => request<PageSummary[]>('/api/pages', filter),
  sources: () => request<SourceSummary[]>('/api/sources'),
  importMarkdown: (file: File) =>
    request<AddedSource>(
      '/api/sources/markdown',
      { filename: file.name },
      { method: 'POST', body: file, headers: { 'Content-Type': 'text/markdown' } },
    ),
  page: (id: string) => request<Page>(`/api/pages/${pagePath(id)}`),
  links: (id: string) => request<Links>(`/api/links/${pagePath(id)}`),
  search: (q: string, k = 20) => request<SearchHit[]>('/api/search', { q, k }),
  source: (hash: string, lines: { start?: number; end?: number } = {}) =>
    request<Source>(`/api/sources/${encodeURIComponent(hash)}`, lines),
  proposals: (status?: string) => request<Proposal[]>('/api/proposals', { status }),
  proposal: (id: string) => request<Proposal>(`/api/proposals/${encodeURIComponent(id)}`),
  accept: (id: string, revision: AcceptRequest = {}) =>
    request<Decision>(`/api/proposals/${encodeURIComponent(id)}/accept`, {}, post(revision)),
  reject: (id: string, reason: string) =>
    request<Decision>(`/api/proposals/${encodeURIComponent(id)}/reject`, {}, post({ reason })),
  jobs: () => request<JobRun[]>('/api/jobs'),
  ingest: (source: string) => request<JobRun>('/api/jobs/ingest', {}, post({ source })),
}
