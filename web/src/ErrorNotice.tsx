import { ApiError } from './api/client'

export function ErrorNotice({ error }: { error: Error }) {
  const title = error instanceof ApiError && error.status === 404 ? 'Not found' : 'Something went wrong'
  return (
    <div role="alert" className="error">
      <strong>{title}</strong>
      <p>{error.message}</p>
    </div>
  )
}
