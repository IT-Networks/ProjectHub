import { useEffect, useState, useMemo } from 'react'
import { useTodoStore } from '@/stores/todoStore'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { STATUS_LABELS, PRIORITY_LABELS } from '@/lib/types'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { cn } from '@/lib/utils'

interface Props {
  projectId: string
}

export function TodoList({ projectId }: Props) {
  const todos = useTodoStore((s) => s.todos)
  const fetchTodos = useTodoStore((s) => s.fetchTodos)
  const createTodo = useTodoStore((s) => s.createTodo)
  const updateStatus = useTodoStore((s) => s.updateStatus)
  const deleteTodo = useTodoStore((s) => s.deleteTodo)
  const [createOpen, setCreateOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [form, setForm] = useState({ title: '', description: '', priority: 'medium', deadline: '' })

  const projectTodos = useMemo(() => todos.filter((t) => t.project_id === projectId), [todos, projectId])

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

      <div className="space-y-2">
        {projectTodos.map((todo) => {
          const isOverdue = todo.deadline && new Date(todo.deadline) < new Date()
          return (
            <div key={todo.id} className="flex items-center gap-3 rounded-lg border border-border p-3">
              {/* Quick-Status Toggle */}
              <button
                onClick={() => updateStatus(todo.id, todo.status === 'done' ? 'backlog' : 'done').then(() => fetchTodos(projectId))}
                className={cn(
                  'h-5 w-5 shrink-0 rounded border-2 transition-colors',
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
              <div className="flex items-center gap-2">
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
                  onClick={() => setDeletingId(todo.id)}
                >
                  x
                </Button>
              </div>
            </div>
          )
        })}
        {projectTodos.length === 0 && (
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

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deletingId}
        onOpenChange={() => setDeletingId(null)}
        title="Todo löschen"
        description="Dieses Todo wird unwiderruflich gelöscht."
        confirmLabel="Löschen"
        onConfirm={() => {
          if (deletingId) deleteTodo(deletingId).then(() => fetchTodos(projectId))
          setDeletingId(null)
        }}
      />
    </div>
  )
}
