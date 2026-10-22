import { QueryClientProvider, useQuery } from '@tanstack/react-query'
import { BrowserRouter, Link, Route, Routes } from 'react-router'
import { api } from './api/client'
import { Library } from './Library'
import { PageView } from './PageView'
import { makeQueryClient } from './queryClient'

function Header() {
  const vault = useQuery({ queryKey: ['vault'], queryFn: api.vault })
  return (
    <header className="header">
      <Link to="/" className="brand">
        Edda
      </Link>
      <span className="muted">{vault.data?.title}</span>
      <nav className="nav">
        <Link to="/">Library</Link>
        <Link to="/page/log.md">Log</Link>
      </nav>
    </header>
  )
}

export function Shell() {
  return (
    <>
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/page/*" element={<PageView />} />
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
