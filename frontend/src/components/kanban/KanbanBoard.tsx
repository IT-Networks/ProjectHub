import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
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
import { KanbanBulkBar } from './KanbanBulkBar'
import { DensityToggle } from './DensityToggle'
import { useTodoStore } from '@/stores/todoStore'
import { useBulkSelectionStore } from '@/stores/bulkSelectionStore'
import { useToast, ToastContainer } from '@/components/shared/Toast'
import { STATUS_LABELS, type Todo, type TodoStatus } from '@/lib/types'

const COLUMNS: TodoStatus[] = ['backlog', 'in_progress', 'review', 'done']

export function KanbanBoard() {
  const todos = useTodoStore((s) => s.todos)
  const updateStatus = useTodoStore((s) => s.updateStatus)
  const bulkUpdateStatus = useTodoStore((s) => s.bulkUpdateStatus)
  const bulkDelete = useTodoStore((s) => s.bulkDelete)
  const density = useTodoStore((s) => s.kanbanDensity)
  const setDensity = useTodoStore((s) => s.setDensity)
  const cycleDensity = useTodoStore((s) => s.cycleDensity)
  const wipLimits = useTodoStore((s) => s.kanbanWipLimits)

  const selectedIds = useBulkSelectionStore((s) => s.selectedIds)
  const selectItem = useBulkSelectionStore((s) => s.selectItem)
  const toggleItem = useBulkSelectionStore((s) => s.toggleItem)
  const selectAll = useBulkSelectionStore((s) => s.selectAll)
  const deselectAll = useBulkSelectionStore((s) => s.deselectAll)

  const [activeItem, setActiveItem] = useState<Todo | null>(null)
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null)
  const { toasts, removeToast, success, error } = useToast()
  const dragFromStatusRef = useRef<TodoStatus | null>(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const columnData = useMemo(() => {
    const groups: Record<TodoStatus, Todo[]> = { backlog: [], in_progress: [], review: [], done: [] }
    for (const t of todos) {
      if (groups[t.status]) groups[t.status].push(t)
    }
    for (const status of COLUMNS) {
      groups[status].sort((a, b) => a.kanban_order - b.kanban_order)
    }
    return groups
  }, [todos])

  const flatOrder = useMemo(
    () => COLUMNS.flatMap((s) => columnData[s].map((t) => t.id)),
    [columnData],
  )

  const handleSelectToggle = useCallback(
    (id: string, evt: { shift: boolean; ctrlOrMeta: boolean }) => {
      if (evt.shift && lastSelectedId) {
        const a = flatOrder.indexOf(lastSelectedId)
        const b = flatOrder.indexOf(id)
        if (a >= 0 && b >= 0) {
          const [from, to] = a < b ? [a, b] : [b, a]
          selectAll(flatOrder.slice(from, to + 1))
          setLastSelectedId(id)
          return
        }
      }
      if (evt.ctrlOrMeta) {
        toggleItem(id)
        setLastSelectedId(id)
        return
      }
      selectItem(id)
      setLastSelectedId(id)
    },
    [flatOrder, lastSelectedId, selectAll, selectItem, toggleItem],
  )

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const todo = todos.find((t) => t.id === event.active.id)
      if (todo) {
        setActiveItem(todo)
        dragFromStatusRef.current = todo.status
      }
    },
    [todos],
  )

  const handleDragOver = useCallback((_event: DragOverEvent) => {}, [])

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveItem(null)
      const fromStatus = dragFromStatusRef.current
      dragFromStatusRef.current = null

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
        const newStatus = targetStatus
        try {
          await updateStatus(todoId, newStatus, maxOrder + 1)
          success('Verschoben', {
            description: `${todo.title} → ${STATUS_LABELS[newStatus] ?? newStatus}`,
            action: fromStatus
              ? {
                  label: 'Rückgängig',
                  onClick: () => {
                    const originTodos = todos.filter((t) => t.status === fromStatus)
                    const originMax = originTodos.reduce((m, t) => Math.max(m, t.kanban_order), 0)
                    void updateStatus(todoId, fromStatus, originMax + 1)
                  },
                }
              : undefined,
          })
        } catch (e) {
          error('Verschieben fehlgeschlagen', {
            description: e instanceof Error ? e.message : String(e),
          })
        }
      }
    },
    [todos, updateStatus, success, error],
  )

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      const isFormElement = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable
      if (isFormElement) return

      if (e.key === 'Escape' && selectedIds.size > 0) {
        e.preventDefault()
        deselectAll()
        setLastSelectedId(null)
        return
      }
      if (e.key === ']' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        cycleDensity(1)
        return
      }
      if (e.key === '[' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        cycleDensity(-1)
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'a' && !e.shiftKey) {
        e.preventDefault()
        selectAll(flatOrder)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [cycleDensity, deselectAll, flatOrder, selectAll, selectedIds.size])

  const handleBulkMove = useCallback(
    async (status: TodoStatus) => {
      await bulkUpdateStatus(Array.from(selectedIds), status)
      deselectAll()
    },
    [bulkUpdateStatus, deselectAll, selectedIds],
  )

  const handleBulkDelete = useCallback(async () => {
    await bulkDelete(Array.from(selectedIds))
    deselectAll()
  }, [bulkDelete, deselectAll, selectedIds])

  return (
    <>
      <div className="mb-3 flex items-center justify-between px-1">
        <div className="text-xs text-muted-foreground">
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5">[</kbd>
          <span className="mx-1">/</span>
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5">]</kbd>
          <span className="ml-1.5">Dichte</span>
          <span className="mx-3 text-border">·</span>
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5">Shift</kbd>
          <span className="mx-1">+</span>
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5">Klick</kbd>
          <span className="ml-1.5">Mehrfachauswahl</span>
        </div>
        <DensityToggle value={density} onChange={setDensity} />
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
      >
        <div className="flex gap-4 overflow-x-auto pb-20">
          {COLUMNS.map((status) => (
            <KanbanColumn
              key={status}
              status={status}
              todos={columnData[status]}
              wipLimit={wipLimits[status]}
              density={density}
              selectedIds={selectedIds}
              onSelectToggle={handleSelectToggle}
            />
          ))}
        </div>

        <DragOverlay>
          {activeItem ? <KanbanCard todo={activeItem} overlay density={density} /> : null}
        </DragOverlay>
      </DndContext>

      <KanbanBulkBar
        count={selectedIds.size}
        onMoveTo={handleBulkMove}
        onDelete={handleBulkDelete}
        onClear={() => {
          deselectAll()
          setLastSelectedId(null)
        }}
      />
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </>
  )
}
