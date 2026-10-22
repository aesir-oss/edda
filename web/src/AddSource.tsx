import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from './api/client'

/** Imports a Markdown file into raw/, unedited, then offers to ingest it: the model's changes arrive for review. */
export function AddSource() {
  const queryClient = useQueryClient()
  const upload = useMutation({
    mutationFn: api.importMarkdown,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sources'] }),
  })
  const ingest = useMutation({
    mutationFn: api.ingest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
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
            if (file) upload.mutate(file)
            e.target.value = ''
          }}
        />
      </label>
      <span role="status" className="muted">
        {upload.isPending && 'Adding…'}
        {upload.data &&
          (upload.data.added
            ? `Added ${upload.data.source.title ?? upload.data.source.hash.slice(0, 12)}`
            : 'Already stored')}
      </span>
      {upload.data && !ingest.data && (
        <button
          type="button"
          className="button"
          disabled={ingest.isPending}
          onClick={() => upload.data && ingest.mutate(upload.data.source.hash)}
        >
          Ingest
        </button>
      )}
      {ingest.data && (
        <span className="muted">
          Ingesting; its proposals will appear in <Link to="/review">Review</Link>.
        </span>
      )}
      {(upload.error ?? ingest.error) && (
        <span role="alert" className="error">
          {(upload.error ?? ingest.error)?.message}
        </span>
      )}
    </div>
  )
}
