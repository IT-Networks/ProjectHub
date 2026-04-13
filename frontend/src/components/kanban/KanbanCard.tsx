import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Badge } from '@/components/ui/badge'
import { PRIORITY_LABELS } from '@/lib/types'
import type { Todo } from '@/lib/types'
import { cn } from '@/lib/utils'

const PRIORITY_COLORS: Record<string, string> = {
  high: 'bg-red-500/20 text-red-400 border-red-500/30',
  medium: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  low: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
}

interface Props {
  todo: Todo
  overlay?: boolean
}

export function KanbanCard({ todo, overlay }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: todo.id,
    data: { type: 'todo', todo },
  })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  const isOverdue = todo.deadline && new Date(todo.deadline) < new Date()

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      className={cn(
        'cursor-grab rounded-lg border border-border bg-card p-3 shadow-sm transition-shadow hover:shadow-md',
        isDragging && 'opacity-50',
        overlay && 'shadow-lg ring-2 ring-primary/20'
      )}
    >
      <p className="text-sm font-medium leading-tight">{todo.title}</p>

      {todo.description && (
        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{todo.description}</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className={cn('text-xs', PRIORITY_COLORS[todo.priority])}>
          {PRIORITY_LABELS[todo.priority]}
        </Badge>

        {todo.deadline && (
          <span className={cn('text-xs', isOverdue ? 'text-red-400' : 'text-muted-foreground')}>
            {new Date(todo.deadline).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })}
          </span>
        )}

        {todo.tags.map((tag) => (
          <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
        ))}
      </div>
    </div>
  )
}
