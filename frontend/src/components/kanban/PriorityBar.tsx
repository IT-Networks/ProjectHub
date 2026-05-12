import type { Priority } from '@/lib/types'
import { cn } from '@/lib/utils'

const COLOR: Record<Priority, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-slate-400',
}

interface Props {
  priority: Priority
  className?: string
}

export function PriorityBar({ priority, className }: Props) {
  return (
    <span
      aria-hidden
      className={cn('absolute inset-y-1 left-0 w-[3px] rounded-full', COLOR[priority], className)}
    />
  )
}
