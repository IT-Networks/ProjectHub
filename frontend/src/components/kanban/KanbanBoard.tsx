import { useState, useMemo, useCallback } from 'react'
import {
  DndContext,
  DragOverlay,
  closestCorners,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragOverEvent,
} from '@dnd-kit/core'
import { KanbanColumn } from './KanbanColumn'
import { KanbanCard } from './KanbanCard'
import { useTodoStore } from '@/stores/todoStore'
import type { Todo, TodoStatus } from '@/lib/types'

const COLUMNS: TodoStatus[] = ['backlog', 'in_progress', 'review', 'done']

export function KanbanBoard() {
  const { todos, updateStatus } = useTodoStore()
  const [activeItem, setActiveItem] = useState<Todo | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const todo = todos.find((t) => t.id === event.active.id)
    if (todo) setActiveItem(todo)
  }, [todos])

  const handleDragOver = useCallback((_event: DragOverEvent) => {}, [])

  const handleDragEnd = useCallback(async (event: DragEndEvent) => {
    setActiveItem(null)

    const { active, over } = event
    if (!over) return

    const todoId = active.id as string
    const todo = todos.find((t) => t.id === todoId)
    if (!todo) return

    let targetStatus: TodoStatus | null = null

    if (COLUMNS.includes(over.id as TodoStatus)) {
      targetStatus = over.id as TodoStatus
    } else {
      const overTodo = todos.find((t) => t.id === over.id)
      if (overTodo) targetStatus = overTodo.status
    }

    if (targetStatus && targetStatus !== todo.status) {
      const targetTodos = todos.filter((t) => t.status === targetStatus)
      const maxOrder = targetTodos.reduce((max, t) => Math.max(max, t.kanban_order), 0)
      await updateStatus(todoId, targetStatus, maxOrder + 1)
    }
  }, [todos, updateStatus])

  const backlog = useMemo(() => todos.filter((t) => t.status === 'backlog').sort((a, b) => a.kanban_order - b.kanban_order), [todos])
  const inProgress = useMemo(() => todos.filter((t) => t.status === 'in_progress').sort((a, b) => a.kanban_order - b.kanban_order), [todos])
  const review = useMemo(() => todos.filter((t) => t.status === 'review').sort((a, b) => a.kanban_order - b.kanban_order), [todos])
  const done = useMemo(() => todos.filter((t) => t.status === 'done').sort((a, b) => a.kanban_order - b.kanban_order), [todos])
  const columnData: Record<TodoStatus, Todo[]> = { backlog, in_progress: inProgress, review, done }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
    >
      <div className="flex gap-4 overflow-x-auto pb-4">
        {COLUMNS.map((status) => (
          <KanbanColumn key={status} status={status} todos={columnData[status]} />
        ))}
      </div>

      <DragOverlay>
        {activeItem ? <KanbanCard todo={activeItem} overlay /> : null}
      </DragOverlay>
    </DndContext>
  )
}
