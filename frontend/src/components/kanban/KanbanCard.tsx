import type { KeyboardEvent, MouseEvent } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Badge } from '@/components/ui/badge'
import type { Todo } from '@/lib/types'
import type { KanbanDensity } from '@/stores/todoStore'
import { cn } from '@/lib/utils'
import { MOTION } from '@/lib/design-system'
import { PriorityBar } from './PriorityBar'
import { DeadlineChip } from './DeadlineChip'
import { AssigneeAvatar } from './AssigneeAvatar'

interface Props {
  todo: Todo
  overlay?: boolean
  density?: KanbanDensity
  selected?: boolean
  onSelectToggle?: (id: string, event: { shift: boolean; ctrlOrMeta: boolean }) => void
}

const PAD: Record<KanbanDensity, string> = {
  compact: 'py-1.5 pl-3 pr-2',
  comfortable: 'py-2.5 pl-3 pr-2.5',
  spacious: 'py-3.5 pl-4 pr-3',
}

const GAP: Record<KanbanDensity, string> = {
  compact: 'mt-1 gap-1',
  comfortable: 'mt-2 gap-1.5',
  spacious: 'mt-2.5 gap-2',
}

export function KanbanCard({ todo, overlay, density = 'comfortable', selected, onSelectToggle }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: todo.id,
    data: { type: 'todo', todo },
  })

  const handleClick = (e: MouseEvent<HTMLDivElement>) => {
    if (!onSelectToggle) return
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      e.preventDefault()
      onSelectToggle(todo.id, { shift: e.shiftKey, ctrlOrMeta: e.ctrlKey || e.metaKey })
    }
  }

  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!onSelectToggle) return
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault()
      onSelectToggle(todo.id, { shift: e.shiftKey, ctrlOrMeta: e.ctrlKey || e.metaKey })
    }
  }

  const showDescription = density !== 'compact' && todo.description
  const showTags = density !== 'compact' && todo.tags.length > 0
  const visibleTags = todo.tags.slice(0, density === 'compact' ? 0 : 3)
  const extraTags = todo.tags.length - visibleTags.length

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        viewTransitionName: overlay ? undefined : MOTION.viewTransitionName.card(todo.id),
      }}
      {...attributes}
      {...listeners}
      role="button"
      tabIndex={overlay ? -1 : 0}
      aria-label={`Todo: ${todo.title}`}
      aria-selected={selected || undefined}
      aria-grabbed={isDragging || undefined}
      onClick={handleClick}
      onKeyDown={handleKey}
      className={cn(
        'group relative cursor-grab select-none rounded-lg border bg-card shadow-sm',
        'transition-[box-shadow,border-color,background-color] duration-[var(--motion-duration-fast)] ease-[var(--motion-ease-out)]',
        'hover:shadow-md hover:border-border',
        'outline-none focus-visible:ring-2 focus-visible:ring-brand/40',
        PAD[density],
        isDragging && 'opacity-50',
        overlay && 'shadow-lg ring-2 ring-brand/40',
        selected
          ? 'border-brand ring-2 ring-brand/30 bg-brand-subtle'
          : 'border-border',
      )}
    >
      <PriorityBar priority={todo.priority} />

      <p className={cn(
        'font-medium leading-tight pl-1',
        density === 'compact' ? 'text-[13px]' : 'text-sm',
      )}>
        {todo.title}
      </p>

      {showDescription && (
        <p className="mt-1 line-clamp-2 pl-1 text-xs text-muted-foreground">
          {todo.description}
        </p>
      )}

      <div className={cn('flex flex-wrap items-center pl-1', GAP[density])}>
        {todo.deadline && <DeadlineChip deadline={todo.deadline} />}

        {(todo.assignee || todo.assignee_id) && (
          <AssigneeAvatar user={todo.assignee ?? undefined} userId={todo.assignee_id ?? null} />
        )}

        {showTags && visibleTags.map((tag) => (
          <Badge key={tag} variant="secondary" className="px-1.5 py-0 text-[10px] font-normal">
            {tag}
          </Badge>
        ))}

        {showTags && extraTags > 0 && (
          <span className="text-[10px] text-muted-foreground">+{extraTags}</span>
        )}
      </div>
    </div>
  )
}
