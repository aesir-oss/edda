import type { Page, PageSummary, Proposal } from './api/client'

export const pages: PageSummary[] = [
  {
    id: 'attention.md',
    title: 'Attention mechanism',
    description: 'How weights work.',
    type: 'Concept',
    tags: ['ml'],
    status: 'stable',
    stale: false,
    sources: ['887e1bdc', 'd9ca93c3'],
  },
  { id: 'bm25.md', title: 'BM25', description: null, type: 'Concept', tags: ['search'], status: 'draft', stale: true, sources: ['d4d4516d'] },
  { id: 'papers/aiayn.md', title: 'Attention Is All You Need', type: null, status: 'stable', stale: false },
]

export const sources = [
  { hash: '887e1bdc', title: 'Reading notes', url: null, added: null, driver: 'markdown' },
  { hash: 'd4d4516d', title: null, url: 'https://en.wikipedia.org/wiki/Okapi_BM25', added: null, driver: 'markdown' },
]

export const attention: Page = {
  id: 'attention.md',
  title: 'Attention mechanism',
  rev: 2,
  frontmatter: { type: 'Concept', tags: ['ml'], status: 'stable' },
  body: '',
  html:
    '<h1>Attention mechanism</h1><p>Weights.<sup class="footnote-reference"><a href="#paper">1</a></sup>' +
    '<sup class="footnote-reference"><a href="#unknown">2</a></sup> See <a href="/page/bm25.md">BM25</a> and ' +
    '<a href="https://example.org">the web</a>.</p>',
  citations: [
    { id: 'paper', resource: 'https://arxiv.org/abs/1706.03762', title: 'The paper', note: 'Section 3.2.', raw: 'd9ca', cited: true },
    { id: 'notes', resource: 'raw:887e', title: null, note: null, raw: null, cited: false },
  ],
  issues: ['source notes is never cited'],
}

const preview = { diff: '--- a/bm25.md\n+++ b/bm25.md\n@@ -1 +1 @@\n-orphan.\n+orphan. Zebras graze.\n context\n', issues: [] }

export const editProposal: Proposal = {
  id: 'prop_000001',
  job: 'ingest:ab12',
  created: '2026-10-22T10:00:00Z',
  change: { kind: 'edit', page: 'bm25.md', base_rev: 1, edits: [{ old: 'orphan.', new: 'orphan. Zebras graze.' }] },
  citations: [
    { id: 'notes-l3', claim: 'Zebras graze.', raw: 'ab12', resource: 'raw:ab12', title: 'Notes', locator: 'lines 3-4' },
    { id: 'notes-s6', claim: 'graze', raw: 'ab12', resource: 'raw:ab12', title: null, locator: 'Section 6' },
  ],
  rationale: 'adds zebras',
  status: 'pending',
  preview,
}

export const pageProposal: Proposal = {
  id: 'prop_000002',
  job: 'ingest:ab12',
  created: '2026-10-22T10:01:00Z',
  change: { kind: 'new_page', page: 'zebra.md', type: 'Concept', title: 'Zebra', description: 'Striped.', body: 'Zebras.\n' },
  rationale: 'a new page',
  status: 'pending',
  preview: { diff: '+Zebras.\n', issues: ['added text cites no source: "Zebras."'] },
}

export const flagProposal: Proposal = {
  id: 'prop_000003',
  job: 'lint',
  created: '2026-10-22T10:02:00Z',
  change: { kind: 'flag', page: 'bm25.md', flag: 'gap', note: 'No example.' },
  citations: [],
  rationale: 'gap',
  status: 'pending',
  preview: { diff: '', issues: [] },
}
