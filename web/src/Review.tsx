import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type AcceptRequest, type PageRevision, type Proposal, type ProposalCitation } from './api/client'
import { ErrorNotice } from './ErrorNotice'
import { JobStatus } from './JobStatus'
import { lineRange, pageOf } from './locator'

const KINDS = { edit: 'Edit', new_page: 'New page', flag: 'Flag' } as const

/** Pending proposals, oldest first, beside the one being reviewed; several can be selected and accepted together. */
export function Review() {
  const selected = useParams().id
  const queryClient = useQueryClient()
  const proposals = useQuery({ queryKey: ['proposals', 'pending'], queryFn: () => api.proposals('pending') })
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: api.jobs })
  const [chosen, setChosen] = useState<string[]>([])
  const together = useMutation({
    mutationFn: api.acceptTogether,
    onSuccess: () => {
      setChosen([])
      void queryClient.invalidateQueries({ queryKey: ['proposals'] })
      void queryClient.invalidateQueries({ queryKey: ['pages'] })
      void queryClient.invalidateQueries({ queryKey: ['page'] })
    },
  })
  const choose = (ids: string[], on: boolean) =>
    setChosen((now) => (on ? [...new Set([...now, ...ids])] : now.filter((id) => !ids.includes(id))))

  if (proposals.error) return <ErrorNotice error={proposals.error} />
  const pending = proposals.data ?? []
  const current = selected ?? pending[0]?.id
  const live = chosen.filter((id) => pending.some((p) => p.id === id))
  return (
    <div className="review-layout">
      <nav aria-label="Pending proposals">
        <p className="muted">{proposals.isPending ? 'Loading…' : `${pending.length} pending`}</p>
        <JobStatus jobs={jobs.data ?? []} />
        {live.length >= 2 && (
          <button
            type="button"
            className="button button-primary"
            disabled={together.isPending}
            onClick={() => together.mutate(live)}
          >
            Accept {live.length} together
          </button>
        )}
        {together.error && (
          <p role="alert" className="error">
            {together.error.message}
          </p>
        )}
        <ul className="list">
          {pending.map((p) => (
            <li key={p.id} className={p.id === current ? 'row row-selected' : 'row'}>
              <input
                type="checkbox"
                aria-label={`Select ${p.change.page}`}
                checked={chosen.includes(p.id)}
                onChange={(e) => choose([p.id], e.target.checked)}
              />{' '}
              <Link to={`/review/${p.id}`} className="row-title">
                {p.change.page}
              </Link>
              <span className="badge">{KINDS[p.change.kind]}</span>
              {p.preview.issues.length > 0 && <span className="badge badge-warn">blocked</span>}
              <p className="row-text">{p.rationale}</p>
            </li>
          ))}
        </ul>
        {pending.length === 0 && !proposals.isPending && <p className="muted">Nothing to review.</p>}
      </nav>
      {current ? (
        <ProposalReview key={current} id={current} pending={pending} onSelect={(ids) => choose(ids, true)} />
      ) : (
        <div />
      )}
    </div>
  )
}

type Related = { pending: Proposal[]; onSelect: (ids: string[]) => void }

function ProposalReview({ id, pending, onSelect }: { id: string } & Related) {
  const proposal = useQuery({ queryKey: ['proposal', id], queryFn: () => api.proposal(id) })
  if (proposal.error) return <ErrorNotice error={proposal.error} />
  if (!proposal.data) return <p className="muted">Loading proposal…</p>
  return <ProposalDetail proposal={proposal.data} pending={pending} onSelect={onSelect} />
}

function ProposalDetail({ proposal, pending, onSelect }: { proposal: Proposal } & Related) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [reason, setReason] = useState('')
  const [revision, setRevision] = useState<AcceptRequest>({})
  const change = proposal.change
  const citations = proposal.citations ?? []
  const decided = () => {
    void queryClient.invalidateQueries({ queryKey: ['proposals'] })
    void queryClient.invalidateQueries({ queryKey: ['pages'] })
    void queryClient.invalidateQueries({ queryKey: ['page', change.page] })
    navigate('/review')
  }
  const accept = useMutation({ mutationFn: () => api.accept(proposal.id, editing ? revision : {}), onSuccess: decided })
  const reject = useMutation({ mutationFn: () => api.reject(proposal.id, reason), onSuccess: decided })
  const busy = accept.isPending || reject.isPending
  const error = accept.error ?? reject.error
  const partners = needs(proposal, pending)

  return (
    <article aria-label={`Proposal ${proposal.id}`}>
      <h1>
        {KINDS[change.kind]}: {change.kind === 'new_page' ? change.page : <Link to={`/page/${change.page}`}>{change.page}</Link>}
      </h1>
      <p className="muted">
        {proposal.id} · by {proposal.job} · {proposal.created}
      </p>
      <p>{proposal.rationale}</p>
      {change.kind === 'flag' && (
        <p>
          <span className="badge badge-warn">{change.flag}</span> {change.note}
        </p>
      )}
      {proposal.preview.issues.length > 0 && (
        <ul className="issues" aria-label="Issues">
          {proposal.preview.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
      {partners.length > 0 && (
        <p className="together">
          It links to {partners.map((p) => p.change.page).join(', ')}, which{' '}
          {partners.length === 1 ? 'another proposal creates' : 'other proposals create'}: accept them together.{' '}
          <button
            type="button"
            className="button"
            onClick={() => onSelect([proposal.id, ...partners.map((p) => p.id)])}
          >
            Select {partners.length === 1 ? 'both' : `all ${partners.length + 1}`}
          </button>
        </p>
      )}
      {editing ? <Editor proposal={proposal} onChange={setRevision} /> : <Diff text={proposal.preview.diff} />}
      {citations.length > 0 && (
        <section aria-label="Citations">
          <h2>Citations</h2>
          {citations.map((c) => (
            <CitationCheck key={c.id} citation={c} />
          ))}
        </section>
      )}
      <div className="actions">
        <button type="button" className="button button-primary" disabled={busy} onClick={() => accept.mutate()}>
          {editing ? 'Accept my version' : 'Accept'}
        </button>
        {change.kind !== 'flag' && (
          <button type="button" className="button" disabled={busy} onClick={() => {
              setEditing(!editing)
              setRevision({})
            }}>
            {editing ? 'Discard my changes' : 'Edit'}
          </button>
        )}
        <input
          aria-label="Reason for rejecting"
          placeholder="Why reject?"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <button type="button" className="button" disabled={busy || !reason.trim()} onClick={() => reject.mutate()}>
          Reject
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error.message}
        </p>
      )}
    </article>
  )
}

const BROKEN = /^link \/(.+) points to a page that does not exist$/

/** The pending new-page proposals that would create pages this proposal's broken links point to. */
function needs(proposal: Proposal, pending: Proposal[]): Proposal[] {
  const missing = proposal.preview.issues.map((issue) => BROKEN.exec(issue)?.[1]).filter(Boolean)
  return pending.filter(
    (p) => p.id !== proposal.id && p.change.kind === 'new_page' && missing.includes(p.change.page),
  )
}

/** A unified diff, with added and removed lines marked. */
export function Diff({ text }: { text: string }) {
  if (!text) return null
  const lines = text.replace(/\n$/, '').split('\n')
  return (
    <pre className="diff" aria-label="Changes">
      {lines.map((line, i) => {
        const kind =
          line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')
            ? 'diff-meta'
            : line.startsWith('+')
              ? 'diff-add'
              : line.startsWith('-')
                ? 'diff-del'
                : undefined
        return (
          <span key={i} className={kind}>
            {line}
            {'\n'}
          </span>
        )
      })}
    </pre>
  )
}

/** The proposal's own text, editable. Only what the owner changes is sent; the rest stays the model's. */
function Editor({ proposal, onChange }: { proposal: Proposal; onChange: (revision: AcceptRequest) => void }) {
  const change = proposal.change
  const [edits, setEdits] = useState(change.kind === 'edit' ? change.edits : [])
  const [page, setPage] = useState<PageRevision>(
    change.kind === 'new_page' ? { title: change.title, description: change.description, body: change.body } : {},
  )
  if (change.kind === 'edit') {
    return (
      <div className="editor">
        {edits.map((edit, i) => (
          <label key={i}>
            <span className="muted">Replaces: {edit.old}</span>
            <textarea
              aria-label={`Edit ${i + 1}`}
              rows={Math.min(12, edit.new.split('\n').length + 1)}
              value={edit.new}
              onChange={(e) => {
                const next = edits.map((x, j) => (j === i ? { ...x, new: e.target.value } : x))
                setEdits(next)
                onChange({ edits: next })
              }}
            />
          </label>
        ))}
      </div>
    )
  }
  const field = (name: 'title' | 'description' | 'body') => (value: string) => {
    const next = { ...page, [name]: value }
    setPage(next)
    onChange({ page: next })
  }
  return (
    <div className="editor">
      <label>
        <span className="muted">Title</span>
        <input value={page.title ?? ''} onChange={(e) => field('title')(e.target.value)} />
      </label>
      <label>
        <span className="muted">Description</span>
        <input value={page.description ?? ''} onChange={(e) => field('description')(e.target.value)} />
      </label>
      <label>
        <span className="muted">Body</span>
        <textarea rows={14} value={page.body ?? ''} onChange={(e) => field('body')(e.target.value)} />
      </label>
    </div>
  )
}

/** A claim beside the source text it cites, so the owner can check one against the other. */
function CitationCheck({ citation }: { citation: ProposalCitation }) {
  const range = lineRange(citation.locator)
  const page = range ? undefined : pageOf(citation.locator)
  const source = useQuery({
    queryKey: ['source', citation.raw, range?.start, range?.end, page],
    queryFn: () => api.source(citation.raw, range ?? (page ? { page } : {})),
  })
  return (
    <div className="citation">
      <blockquote aria-label="Claim">{citation.claim}</blockquote>
      <div>
        <p className="muted">
          {citation.title ?? citation.resource} · {citation.locator}
        </p>
        {source.error ? (
          <p className="error">{source.error.message}</p>
        ) : range || page ? (
          <pre className="source-text" aria-label="Cited source text">
            {source.data?.text ?? 'Loading…'}
          </pre>
        ) : (
          <details>
            <summary>The locator names no lines; show the whole source</summary>
            <pre className="source-text">{source.data?.text ?? 'Loading…'}</pre>
          </details>
        )}
      </div>
    </div>
  )
}
