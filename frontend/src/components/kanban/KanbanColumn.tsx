import { useDroppable } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { KanbanCard } from './KanbanCard'
import { KanbanColumnHeader } from './KanbanColumnHeader'
import { EmptyStateCompact } from '@/components/shared/EmptyState'
import type { Todo, TodoStatus } from '@/lib/types'
import type { KanbanDensity } from '@/stores/todoStore'
import { cn } from '@/lib/utils'

interface Props {
  status: TodoStatus
  todos: Todo[]
  wipLimit?: number
  density: KanbanDensity
  selectedIds: Set<string>
  onSelectToggle: (id: string, event: { shift: boolean; ctrlOrMeta: boolean }) => void
}

const GAP: Record<KanbanDensity, string> = {
  compact: 'gap-1.5',
  comfortable: 'gap-2',
  spacious: 'gap-3',
}

export function KanbanColumn({ status, todos, wipLimit, density, selectedIds, onSelectToggle }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id: status })

  return (
    <div className="flex min-w-[280px] flex-col">
      <KanbanColumnHeader status={status} count={todos.length} wipLimit={wipLimit} />

      <div
        ref={setNodeRef}
        className={cn(
          'flex flex-1 flex-col rounded-lg border border-dashed p-2 transition-colors',
          GAP[density],
          isOver ? 'border-brand bg-brand-subtle' : 'border-transparent',
        )}
      >
        <SortableContext items={todos.map((t) => t.id)} strategy={verticalListSortingStrategy}>
          {todos.map((todo) => (
            <KanbanCard
              key={todo.id}
              todo={todo}
              density={density}
              selected={selectedIds.has(todo.id)}
              onSelectToggle={onSelectToggle}
            />
          ))}
        </SortableContext>

        {todos.length === 0 && (
          <EmptyStateCompact icon="📭" title="Keine Todos" />
        )}
      </div>
    </div>
  )
}
