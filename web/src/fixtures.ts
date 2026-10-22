import type { Page, PageSummary } from './api/client'

export const pages: PageSummary[] = [
  { id: 'attention.md', title: 'Attention mechanism', description: 'How weights work.', type: 'Concept', tags: ['ml'], status: 'stable' },
  { id: 'bm25.md', title: 'BM25', description: null, type: 'Concept', tags: ['search'], status: 'draft' },
  { id: 'papers/aiayn.md', title: 'Attention Is All You Need', type: null, status: 'stable' },
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
