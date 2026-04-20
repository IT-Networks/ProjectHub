import { useDroppable } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { KanbanCard } from './KanbanCard'
import { EmptyStateCompact } from '@/components/shared/EmptyState'
import { STATUS_LABELS } from '@/lib/types'
import type { Todo, TodoStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface Props {
  status: TodoStatus
  todos: Todo[]
  wipLimit?: number
}

export function KanbanColumn({ status, todos, wipLimit }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id: status })
  const overLimit = wipLimit != null && todos.length > wipLimit

  return (
    <div className="flex min-w-[280px] flex-col">
      {/* Column Header */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">{STATUS_LABELS[status]}</h3>
          <span className={cn(
            'rounded-full px-2 py-0.5 text-xs font-medium',
            overLimit ? 'bg-red-500/20 text-red-400' : 'bg-muted text-muted-foreground'
          )}>
            {todos.length}{wipLimit != null ? `/${wipLimit}` : ''}
          </span>
        </div>
      </div>

      {/* Drop Zone */}
      <div
        ref={setNodeRef}
        className={cn(
          'flex flex-1 flex-col gap-2 rounded-lg border border-dashed p-2 transition-colors',
          isOver ? 'border-primary bg-primary/5' : 'border-transparent'
        )}
      >
        <SortableContext items={todos.map((t) => t.id)} strategy={verticalListSortingStrategy}>
          {todos.map((todo) => (
            <KanbanCard key={todo.id} todo={todo} />
          ))}
        </SortableContext>

        {todos.length === 0 && (
          <EmptyStateCompact
            icon="📭"
            title="Keine Todos"
          />
        )}
      </div>
    </div>
  )
}
