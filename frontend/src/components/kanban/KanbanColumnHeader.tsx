import { STATUS_LABELS, type TodoStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface Props {
  status: TodoStatus
  count: number
  wipLimit?: number
}

export function KanbanColumnHeader({ status, count, wipLimit }: Props) {
  const overLimit = wipLimit != null && count > wipLimit
  const atLimit = wipLimit != null && count === wipLimit

  return (
    <div className="mb-3 flex items-center justify-between px-1">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold tracking-tight">{STATUS_LABELS[status]}</h3>
        <span
          role={overLimit ? 'status' : undefined}
          aria-live={overLimit ? 'polite' : undefined}
          className={cn(
            'rounded-full px-2 py-0.5 text-xs font-medium tabular-nums transition-colors',
            overLimit && 'bg-red-500/15 text-red-500 dark:text-red-400 ring-1 ring-red-500/30 wip-pulse',
            !overLimit && atLimit && 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
            !overLimit && !atLimit && 'bg-muted text-muted-foreground',
          )}
        >
          {count}{wipLimit != null ? `/${wipLimit}` : ''}
        </span>
      </div>
    </div>
  )
}
