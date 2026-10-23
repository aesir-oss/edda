import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { api, type AddedSource } from './api/client'

type Input = { file: File } | { url: string }

const DOCUMENTS = ['.docx', '.pptx', '.xlsx', '.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff']
const isDocument = (file: File) => DOCUMENTS.some((ext) => file.name.toLowerCase().endsWith(ext))

/** What an import added, as the source to ingest: for a document, the Markdown made from it. */
async function addSource(input: Input): Promise<AddedSource> {
  if ('url' in input) return api.importWeb(input.url)
  if (!isDocument(input.file)) return api.importMarkdown(input.file)
  return (await api.importDocument(input.file)).derived
}

/** Adds a Markdown file, a document (Office, PDF or scan) or a web page to raw/, then offers to ingest it. */
export function AddSource() {
  const queryClient = useQueryClient()
  const [url, setUrl] = useState('')
  const ingest = useMutation({
    mutationFn: api.ingest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })
  const add = useMutation({
    mutationFn: addSource,
    onMutate: () => ingest.reset(),
    onSuccess: (_, input) => {
      if ('url' in input) setUrl('')
      return queryClient.invalidateQueries({ queryKey: ['sources'] })
    },
  })

  return (
    <div className="add-source">
      <label className="button">
        Add a file
        <input
          type="file"
          accept={['.md', '.markdown', ...DOCUMENTS].join(',')}
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
        {add.isPending && 'Adding… (documents take a while to convert)'}
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
