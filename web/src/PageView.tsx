import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, type MouseEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type Citation } from './api/client'
import { ErrorNotice } from './ErrorNotice'

function citationText(c: Citation) {
  return [c.title ?? c.resource, c.note].filter(Boolean).join(' · ')
}

/** A rendered page with hover-to-see citations, its sources, and the pages that link to it. */
export function PageView() {
  const id = useParams()['*'] ?? ''
  const navigate = useNavigate()
  const prose = useRef<HTMLDivElement>(null)
  const page = useQuery({ queryKey: ['page', id], queryFn: () => api.page(id) })
  const links = useQuery({ queryKey: ['links', id], queryFn: () => api.links(id) })

  const data = page.data
  useEffect(() => {
    const root = prose.current
    if (!root || !data) return
    for (const ref of root.querySelectorAll<HTMLAnchorElement>('.footnote-reference a')) {
      const label = decodeURIComponent(ref.hash.slice(1))
      const citation = data.citations.find((c) => c.id === label)
      if (citation) ref.title = citationText(citation)
    }
  }, [data])

  // Links between pages are rendered as /page/<id>; follow them inside the app instead of reloading it.
  const onClick = (event: MouseEvent<HTMLElement>) => {
    const anchor = (event.target as HTMLElement).closest('a')
    const href = anchor?.getAttribute('href')
    if (href?.startsWith('/page/') && !event.metaKey && !event.ctrlKey) {
      event.preventDefault()
      navigate(href)
    }
  }

  if (page.error) return <ErrorNotice error={page.error} />
  if (!data) return <p className="muted">Loading page…</p>

  const fm = data.frontmatter as { type?: string; tags?: string[]; status?: string }
  return (
    <div className="page-layout">
      <article>
        <p className="muted">
          {fm.type && <span className="badge">{fm.type}</span>} {data.id}
          {data.rev != null && <> · revision {data.rev}</>}
          {fm.status && <> · {fm.status}</>}
        </p>
        {data.issues.length > 0 && (
          <ul className="issues" aria-label="Issues">
            {data.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
        {/* The kernel escapes raw HTML and drops unsafe link schemes before this point. */}
        <div ref={prose} className="prose" onClick={onClick} dangerouslySetInnerHTML={{ __html: data.html }} />
      </article>
      <aside>
        <h2>Sources</h2>
        {data.citations.length === 0 ? (
          <p className="muted">No sources.</p>
        ) : (
          <ol className="sources">
            {data.citations.map((c) => (
              <li key={c.id} id={`source-${c.id}`}>
                <SourceLink citation={c} />
                {c.note && <span className="muted"> · {c.note}</span>}
              </li>
            ))}
          </ol>
        )}
        <h2>Backlinks</h2>
        {links.data && links.data.backlinks.length > 0 ? (
          <ul className="backlinks">
            {links.data.backlinks.map((b) => (
              <li key={b}>
                <Link to={`/page/${b}`}>{b}</Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{links.isPending ? 'Loading…' : 'No pages link here.'}</p>
        )}
      </aside>
    </div>
  )
}

function SourceLink({ citation }: { citation: Citation }) {
  const label = citation.title ?? citation.resource
  if (/^https?:\/\//.test(citation.resource)) {
    return (
      <a href={citation.resource} rel="noreferrer" target="_blank">
        {label}
      </a>
    )
  }
  return <span title={citation.raw ?? citation.resource}>{label}</span>
}
