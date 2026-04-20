import { useEffect, useState, useMemo } from 'react'
import { useTodoStore } from '@/stores/todoStore'
import { useBulkSelectionStore } from '@/stores/bulkSelectionStore'
import { useToast } from '@/components/shared/Toast'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Checkbox } from '@/components/shared/Checkbox'
import { BatchActionsToolbar } from '@/components/shared/BatchActionsToolbar'
import { STATUS_LABELS, PRIORITY_LABELS } from '@/lib/types'
import type { Todo } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Trash2 } from 'lucide-react'

interface Props {
  projectId: string
  enableBulkSelect?: boolean
}

export function TodoList({ projectId, enableBulkSelect = true }: Props) {
  const todos = useTodoStore((s) => s.todos)
  const fetchTodos = useTodoStore((s) => s.fetchTodos)
  const createTodo = useTodoStore((s) => s.createTodo)
  const updateStatus = useTodoStore((s) => s.updateStatus)
  const deleteTodo = useTodoStore((s) => s.deleteTodo)
  const { success, error } = useToast()
  const {
    isSelectMode,
    toggleItem,
    deselectAll,
    isSelected,
    getSelectedIds,
    getSelectedCount,
  } = useBulkSelectionStore()

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ title: '', description: '', priority: 'medium', deadline: '' })
  const [visibleTodos, setVisibleTodos] = useState<string[]>([])
  const [deletedTodoBackup, setDeletedTodoBackup] = useState<{ id: string; todo: Todo } | null>(null)

  const projectTodos = useMemo(() => todos.filter((t) => t.project_id === projectId), [todos, projectId])
  const displayTodos = useMemo(() => projectTodos.filter((t) => !visibleTodos.includes(t.id) || t.id === deletedTodoBackup?.id), [projectTodos, visibleTodos, deletedTodoBackup])

  const handleBatchDelete = async () => {
    const selectedCount = getSelectedCount()
    if (selectedCount === 0) return

    const confirmed = window.confirm(
      `Wirklich ${selectedCount} Todo(s) löschen?`
    )
    if (!confirmed) return

    try {
      const ids = getSelectedIds()
      for (const id of ids) {
        await deleteTodo(id)
      }
      success(`${selectedCount} Todo(s) gelöscht!`)
      deselectAll()
      await fetchTodos(projectId)
    } catch (err) {
      error(`Fehler beim Löschen: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
    }
  }

  useEffect(() => {
    fetchTodos(projectId)
  }, [fetchTodos, projectId])

  const handleCreate = async () => {
    if (!form.title.trim()) return
    await createTodo({
      title: form.title,
      description: form.description,
      priority: form.priority,
      deadline: form.deadline || null,
      project_id: projectId,
    })
    setForm({ title: '', description: '', priority: 'medium', deadline: '' })
    setCreateOpen(false)
    await fetchTodos(projectId)
  }

  const handleDelete = async (todoId: string) => {
    const todoToDelete = projectTodos.find((t) => t.id === todoId)
    if (!todoToDelete) return

    // Optimistic delete - remove from UI immediately
    setDeletedTodoBackup({ id: todoId, todo: todoToDelete })
    setVisibleTodos((prev) => [...prev, todoId])

    // Show undo toast
    success('Todo gelöscht', {
      action: {
        label: 'Rückgängig',
        onClick: () => {
          setVisibleTodos((prev) => prev.filter((id) => id !== todoId))
          setDeletedTodoBackup(null)
        },
      },
      duration: 5000,
    })

    // Actually delete after a short delay (gives user time to undo)
    setTimeout(async () => {
      try {
        await deleteTodo(todoId)
        setDeletedTodoBackup(null)
      } catch (err) {
        // Restore on error
        setVisibleTodos((prev) => prev.filter((id) => id !== todoId))
        setDeletedTodoBackup(null)
        error('Fehler beim Löschen des Todos')
      }
    }, 5000)
  }

  const statusColors: Record<string, string> = {
    backlog: 'bg-muted',
    in_progress: 'bg-blue-500/20 text-blue-400',
    review: 'bg-yellow-500/20 text-yellow-400',
    done: 'bg-green-500/20 text-green-400',
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{projectTodos.length} Todos</span>
        <Button size="sm" onClick={() => setCreateOpen(true)}>+ Todo</Button>
      </div>

      {enableBulkSelect && isSelectMode && (
        <BatchActionsToolbar
          selectedCount={getSelectedCount()}
          totalCount={projectTodos.length}
          onClearSelection={deselectAll}
          actions={[
            {
              id: 'delete',
              label: `Löschen (${getSelectedCount()})`,
              icon: <Trash2 className="h-4 w-4" />,
              onClick: handleBatchDelete,
              variant: 'destructive',
              disabled: getSelectedCount() === 0,
            },
          ]}
          compact
          className="mb-4"
        />
      )}

      <div className="space-y-2">
        {displayTodos.map((todo) => {
          const isOverdue = todo.deadline && new Date(todo.deadline) < new Date()
          const itemSelected = enableBulkSelect && isSelectMode && isSelected(todo.id)

          return (
            <div
              key={todo.id}
              className={`flex items-start gap-3 rounded-lg border p-3 transition-colors ${
                itemSelected ? 'border-primary bg-primary/5' : 'border-border'
              }`}
              onClick={() => {
                if (enableBulkSelect && isSelectMode) {
                  toggleItem(todo.id)
                }
              }}
            >
              {enableBulkSelect && isSelectMode && (
                <Checkbox
                  checked={itemSelected}
                  onChange={(checked) => {
                    if (checked) {
                      toggleItem(todo.id)
                    } else {
                      toggleItem(todo.id)
                    }
                  }}
                  className="flex-shrink-0 mt-1"
                  ariaLabel={`Select ${todo.title}`}
                />
              )}
              <button
                onClick={() => updateStatus(todo.id, todo.status === 'done' ? 'backlog' : 'done').then(() => fetchTodos(projectId))}
                className={cn(
                  'h-5 w-5 shrink-0 rounded border-2 transition-colors mt-0.5',
                  todo.status === 'done' ? 'border-green-500 bg-green-500' : 'border-muted-foreground'
                )}
              />
              <div className="flex-1 min-w-0">
                <p className={cn('text-sm font-medium', todo.status === 'done' && 'line-through text-muted-foreground')}>
                  {todo.title}
                </p>
                {todo.description && (
                  <p className="mt-0.5 text-xs text-muted-foreground line-clamp-1">{todo.description}</p>
                )}
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <Badge variant="outline" className={cn('text-xs', statusColors[todo.status])}>
                  {STATUS_LABELS[todo.status]}
                </Badge>
                <Badge variant="outline" className="text-xs">
                  {PRIORITY_LABELS[todo.priority]}
                </Badge>
                {todo.deadline && (
                  <span className={cn('text-xs', isOverdue ? 'text-red-400' : 'text-muted-foreground')}>
                    {new Date(todo.deadline).toLocaleDateString('de-DE')}
                  </span>
                )}
                <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-muted-foreground hover:text-red-400"
                  aria-label="Todo löschen"
                  onClick={() => handleDelete(todo.id)}
                >
                  x
                </Button>
              </div>
            </div>
          )
        })}
        {displayTodos.length === 0 && projectTodos.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">Noch keine Todos</p>
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Neues Todo</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="mb-1 block text-sm font-medium">Titel</label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Todo..." autoFocus />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Beschreibung</label>
              <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} />
            </div>
            <div className="flex gap-4">
              <div className="flex-1">
                <label className="mb-1 block text-sm font-medium">Priorität</label>
                <Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="high">Hoch</SelectItem>
                    <SelectItem value="medium">Mittel</SelectItem>
                    <SelectItem value="low">Niedrig</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex-1">
                <label className="mb-1 block text-sm font-medium">Frist</label>
                <Input type="date" value={form.deadline} onChange={(e) => setForm({ ...form, deadline: e.target.value })} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Abbrechen</Button>
            <Button onClick={handleCreate} disabled={!form.title.trim()}>Erstellen</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
