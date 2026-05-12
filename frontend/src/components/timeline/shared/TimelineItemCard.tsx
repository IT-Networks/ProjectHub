import type { KeyboardEvent, MouseEvent } from 'react'
import { CheckSquare, FileText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { PriorityBar } from '@/components/kanban/PriorityBar'
import { DeadlineChip } from '@/components/kanban/DeadlineChip'
import { AssigneeAvatar } from '@/components/kanban/AssigneeAvatar'
import type { TimelineItem } from '@/lib/timeline'
import { cn } from '@/lib/utils'

interface Props {
  item: TimelineItem
  density?: 'compact' | 'comfortable'
  onClick?: (item: TimelineItem) => void
  now?: Date
}

export function TimelineItemCard({ item, density = 'comfortable', onClick, now = new Date() }: Props) {
  const isTodo = item.kind === 'todo'
  const Icon = isTodo ? CheckSquare : FileText
  const showDescription = density === 'comfortable' && item.description

  const handleClick = (e: MouseEvent<HTMLDivElement>) => {
    if (!onClick) return
    e.preventDefault()
    onClick(item)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!onClick) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onClick(item)
    }
  }

  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      aria-label={`${isTodo ? 'Todo' : 'Notiz'}: ${item.title}`}
      className={cn(
        'group relative rounded-lg border border-border bg-card transition-colors',
        density === 'compact' ? 'py-1.5 pl-3 pr-2.5' : 'py-2.5 pl-3 pr-3',
        onClick && 'cursor-pointer hover:bg-accent/50 hover:border-border focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40',
        item.completed && 'opacity-60',
      )}
    >
      {isTodo && item.priority && <PriorityBar priority={item.priority} />}

      <div className={cn('flex items-start gap-2', !isTodo && 'pl-1.5')}>
        <Icon
          aria-hidden
          className={cn(
            'mt-0.5 h-3.5 w-3.5 shrink-0',
            isTodo ? 'text-muted-foreground' : 'text-brand',
          )}
        />

        <div className="min-w-0 flex-1">
          <p
            className={cn(
              'font-medium leading-tight',
              density === 'compact' ? 'text-[13px]' : 'text-sm',
              item.completed && 'line-through',
            )}
          >
            {item.title}
          </p>

          {showDescription && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
              {item.description}
            </p>
          )}

          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <DeadlineChip deadline={item.at} now={now} />

            {item.project_name && (
              <Badge
                variant="outline"
                className="gap-1 px-1.5 py-0 text-[10px] font-normal"
                style={item.project_color ? { borderColor: item.project_color } : undefined}
              >
                {item.project_color && (
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ backgroundColor: item.project_color }}
                  />
                )}
                {item.project_name}
              </Badge>
            )}

            {item.tags.slice(0, 3).map((t) => (
              <Badge key={t} variant="secondary" className="px-1.5 py-0 text-[10px] font-normal">
                #{t}
              </Badge>
            ))}
            {item.tags.length > 3 && (
              <span className="text-[10px] text-muted-foreground">+{item.tags.length - 3}</span>
            )}
          </div>
        </div>

        {(item.assignee || item.assignee_id) && (
          <AssigneeAvatar user={item.assignee ?? undefined} userId={item.assignee_id ?? null} />
        )}
      </div>
    </div>
  )
}
