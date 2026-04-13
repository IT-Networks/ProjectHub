interface Props {
  message: string
  onDismiss?: () => void
}

export function ErrorBanner({ message, onDismiss }: Props) {
  if (!message) return null
  return (
    <div className="flex items-center justify-between rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400" role="alert">
      <span>{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="ml-3 text-red-400 hover:text-red-300" aria-label="Fehler schließen">
          x
        </button>
      )}
    </div>
  )
}
