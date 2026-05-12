import { CheckSquare, FileText } from 'lucide-react'
import type { Priority } from '@/lib/types'
import { xForDate, type GanttWindow, type TimelineItem } from '@/lib/timeline'
import { cn } from '@/lib/utils'

const PRIORITY_BG: Record<Priority, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-slate-400',
}

interface Props {
  item: TimelineItem
  window: GanttWindow
  pxPerDay: number
  onClick?: (item: TimelineItem) => void
}

const BAR_HEIGHT = 20

export function GanttBar({ item, window, pxPerDay, onClick }: Props) {
  const at = new Date(item.at)
  if (isNaN(at.getTime())) return null
  const x = xForDate(at, window.from, pxPerDay)

  const isTodo = item.kind === 'todo'
  const Icon = isTodo ? CheckSquare : FileText
  const bg = isTodo && item.priority ? PRIORITY_BG[item.priority] : 'bg-brand'

  const minWidth = Math.max(pxPerDay, 72)
  const label = item.title.length > 30 ? item.title.slice(0, 28) + '…' : item.title

  return (
    <button
      type="button"
      onClick={() => onClick?.(item)}
      title={`${item.title} · ${at.toLocaleDateString('de-DE')}`}
      aria-label={`${isTodo ? 'Todo' : 'Notiz'}: ${item.title} am ${at.toLocaleDateString('de-DE')}`}
      className={cn(
        'absolute flex items-center gap-1 rounded-md px-1.5 text-[11px] font-medium text-white shadow-sm ring-1 ring-black/10',
        'transition-transform hover:-translate-y-px hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand',
        bg,
        item.completed && 'opacity-50 line-through',
      )}
      style={{
        left: x - pxPerDay / 2,
        top: '50%',
        transform: 'translateY(-50%)',
        minWidth,
        height: BAR_HEIGHT,
      }}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      <span className="truncate">{label}</span>
    </button>
  )
}
