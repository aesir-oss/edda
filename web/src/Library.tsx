import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { api, type PageSummary } from './api/client'
import { ErrorNotice } from './ErrorNotice'

function PageRow({ page }: { page: PageSummary }) {
  return (
    <li className="row">
      <Link to={`/page/${page.id}`} className="row-title">
        {page.title}
      </Link>
      {page.type && <span className="badge">{page.type}</span>}
      {page.status !== 'stable' && <span className="badge badge-muted">{page.status}</span>}
      {page.stale && <span className="badge badge-warn">stale</span>}
      {page.description && <p className="row-text">{page.description}</p>}
    </li>
  )
}

/** Browse every page, filter by tag, source or status, or search. The filters live in the URL so a view can be linked. */
export function Library() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const tag = params.get('tag') ?? ''
  const source = params.get('source') ?? ''
  const status = params.get('status') ?? ''
  const pages = useQuery({ queryKey: ['pages'], queryFn: () => api.pages() })
  const sources = useQuery({ queryKey: ['sources'], queryFn: api.sources })
  const hits = useQuery({
    queryKey: ['search', q],
    queryFn: () => api.search(q),
    enabled: q.trim() !== '',
    placeholderData: keepPreviousData,
  })

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const tags = [...new Set((pages.data ?? []).flatMap((p) => p.tags ?? []))].sort()
  const listed = (pages.data ?? []).filter(
    (p) =>
      (!tag || (p.tags ?? []).includes(tag)) &&
      (!source || (p.sources ?? []).includes(source)) &&
      (!status || (status === 'stale' ? p.stale : p.status === status)),
  )

  return (
    <section>
      <div className="toolbar">
        <input
          type="search"
          aria-label="Search pages"
          placeholder="Search pages"
          value={q}
          onChange={(e) => update('q', e.target.value)}
        />
        <select aria-label="Filter by tag" value={tag} onChange={(e) => update('tag', e.target.value)}>
          <option value="">All tags</option>
          {tags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select aria-label="Filter by source" value={source} onChange={(e) => update('source', e.target.value)}>
          <option value="">All sources</option>
          {(sources.data ?? []).map((s) => (
            <option key={s.hash} value={s.hash}>
              {s.title ?? s.hash.slice(0, 12)}
            </option>
          ))}
        </select>
        <select aria-label="Filter by status" value={status} onChange={(e) => update('status', e.target.value)}>
          <option value="">Any status</option>
          <option value="draft">Draft</option>
          <option value="stale">Stale</option>
        </select>
      </div>

      {q.trim() ? (
        <SearchResults loading={hits.isPending} error={hits.error} hits={hits.data} />
      ) : (
        <>
          {pages.error && <ErrorNotice error={pages.error} />}
          {pages.isPending && <p className="muted">Loading pages…</p>}
          {pages.data && (
            <>
              <p className="muted">
                {listed.length} {listed.length === 1 ? 'page' : 'pages'}
              </p>
              <ul className="list">
                {listed.map((p) => (
                  <PageRow key={p.id} page={p} />
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  )
}

function SearchResults({
  loading,
  error,
  hits,
}: {
  loading: boolean
  error: Error | null
  hits: Awaited<ReturnType<typeof api.search>> | undefined
}) {
  if (error) return <ErrorNotice error={error} />
  if (loading || !hits) return <p className="muted">Searching…</p>
  if (hits.length === 0) return <p className="muted">No pages match.</p>
  return (
    <ul className="list" aria-label="Search results">
      {hits.map((hit) => (
        <li key={hit.id} className="row">
          <Link to={`/page/${hit.id}`} className="row-title">
            {hit.title}
          </Link>
          {/* The snippet is HTML-escaped by the kernel; only <b> highlights are markup. */}
          <p className="row-text" dangerouslySetInnerHTML={{ __html: hit.snippet || hit.description }} />
        </li>
      ))}
    </ul>
  )
}
