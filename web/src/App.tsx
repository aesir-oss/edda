import { QueryClientProvider, useQuery } from '@tanstack/react-query'
import { BrowserRouter, Link, Route, Routes } from 'react-router'
import { api } from './api/client'
import { useVaultEvents } from './events'
import { Library } from './Library'
import { PageView } from './PageView'
import { makeQueryClient } from './queryClient'
import { Review } from './Review'

function Header() {
  const vault = useQuery({ queryKey: ['vault'], queryFn: api.vault })
  const pending = useQuery({ queryKey: ['proposals', 'pending'], queryFn: () => api.proposals('pending') })
  const count = pending.data?.length ?? 0
  return (
    <header className="header">
      <Link to="/" className="brand">
        Edda
      </Link>
      <span className="muted">{vault.data?.title}</span>
      <nav className="nav">
        <Link to="/">Library</Link>
        <Link to="/review" aria-label={count > 0 ? `Review, ${count} pending` : undefined}>
          Review{count > 0 && <span className="badge count">{count}</span>}
        </Link>
        <Link to="/page/log.md">Log</Link>
      </nav>
    </header>
  )
}

export function Shell() {
  useVaultEvents()
  return (
    <>
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/page/*" element={<PageView />} />
          <Route path="/review" element={<Review />} />
          <Route path="/review/:id" element={<Review />} />
          <Route path="*" element={<p>Nothing here. <Link to="/">Back to the library</Link>.</p>} />
        </Routes>
      </main>
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
