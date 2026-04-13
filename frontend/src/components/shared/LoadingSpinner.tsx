import { cn } from '@/lib/utils'

interface Props {
  className?: string
  size?: 'sm' | 'md'
  label?: string
}

export function LoadingSpinner({ className, size = 'md', label }: Props) {
  return (
    <div className={cn('flex items-center gap-2 text-muted-foreground', className)} role="status" aria-label={label || 'Laden'}>
      <svg
        className={cn('animate-spin', size === 'sm' ? 'h-4 w-4' : 'h-5 w-5')}
        viewBox="0 0 24 24"
        fill="none"
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      {label && <span className="text-sm">{label}</span>}
    </div>
  )
}
