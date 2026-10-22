import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { api } from './api/client'

type Input = { file: File } | { url: string }

/** Adds a Markdown file or a web page to raw/, then offers to ingest it: the model's changes arrive for review. */
export function AddSource() {
  const queryClient = useQueryClient()
  const [url, setUrl] = useState('')
  const ingest = useMutation({
    mutationFn: api.ingest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })
  const add = useMutation({
    mutationFn: (input: Input) => ('file' in input ? api.importMarkdown(input.file) : api.importWeb(input.url)),
    onMutate: () => ingest.reset(),
    onSuccess: (_, input) => {
      if ('url' in input) setUrl('')
      return queryClient.invalidateQueries({ queryKey: ['sources'] })
    },
  })

  return (
    <div className="add-source">
      <label className="button">
        Add Markdown source
        <input
          type="file"
          accept=".md,.markdown,text/markdown"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) add.mutate({ file })
            e.target.value = ''
          }}
        />
      </label>
      <form
        className="add-web"
        onSubmit={(e) => {
          e.preventDefault()
          if (url.trim()) add.mutate({ url: url.trim() })
        }}
      >
        <input
          type="url"
          aria-label="Web page address"
          placeholder="https://…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button type="submit" className="button" disabled={add.isPending || !url.trim()}>
          Add web page
        </button>
      </form>
      <span role="status" className="muted">
        {add.isPending && 'Adding…'}
        {add.data &&
          (add.data.added ? `Added ${add.data.source.title ?? add.data.source.hash.slice(0, 12)}` : 'Already stored')}
      </span>
      {add.data && !ingest.data && (
        <button
          type="button"
          className="button"
          disabled={ingest.isPending}
          onClick={() => add.data && ingest.mutate(add.data.source.hash)}
        >
          Ingest
        </button>
      )}
      {ingest.data && (
        <span className="muted">
          Ingesting; its proposals will appear in <Link to="/review">Review</Link>.
        </span>
      )}
      {(add.error ?? ingest.error) && (
        <span role="alert" className="error">
          {(add.error ?? ingest.error)?.message}
        </span>
      )}
    </div>
  )
}
