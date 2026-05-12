import type { MouseEvent } from 'react'
import type { Priority } from '@/lib/types'
import type { TimelineItem } from '@/lib/timeline'
import { cn } from '@/lib/utils'

const PRIORITY_DOT: Record<Priority, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-slate-400',
}

interface Props {
  item: TimelineItem
  onClick?: (item: TimelineItem, e: MouseEvent) => void
}

export function ItemDot({ item, onClick }: Props) {
  const handleClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    onClick?.(item, e)
  }

  const dotClass = item.kind === 'todo' && item.priority
    ? PRIORITY_DOT[item.priority]
    : 'bg-brand'

  return (
    <button
      type="button"
      onClick={handleClick}
      title={item.title}
      aria-label={`${item.kind === 'todo' ? 'Todo' : 'Notiz'}: ${item.title}`}
      className={cn(
        'flex w-full items-center gap-1 truncate rounded px-1.5 py-0.5 text-left text-[11px] leading-tight transition-colors',
        'hover:bg-muted focus:outline-none focus-visible:ring-1 focus-visible:ring-brand/60',
        item.completed && 'opacity-50 line-through',
      )}
    >
      <span
        aria-hidden
        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dotClass)}
      />
      <span className="truncate">{item.title}</span>
    </button>
  )
}
