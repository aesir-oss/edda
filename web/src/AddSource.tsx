import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api/client'

/** Imports a Markdown file into raw/, unedited. Ingesting it into pages comes later, through proposals. */
export function AddSource() {
  const queryClient = useQueryClient()
  const upload = useMutation({
    mutationFn: api.importMarkdown,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sources'] }),
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
      {upload.error && (
        <span role="alert" className="error">
          {upload.error.message}
        </span>
      )}
    </div>
  )
}
