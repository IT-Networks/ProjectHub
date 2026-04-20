import { useEffect, useState } from 'react'
import { Plus } from 'lucide-react'
import { KanbanBoard } from '@/components/kanban/KanbanBoard'
import { useTodoStore } from '@/stores/todoStore'
import { useProjectStore } from '@/stores/projectStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { useToast } from '@/components/shared/Toast'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { EmptyState } from '@/components/shared/EmptyState'
import { FormField } from '@/components/shared/FormField'
import { KanbanSkeleton } from '@/components/shared/Skeleton'

export function KanbanPage() {
  const fetchTodos = useTodoStore((s) => s.fetchTodos)
  const createTodo = useTodoStore((s) => s.createTodo)
  const filterProjectId = useTodoStore((s) => s.filterProjectId)
  const setFilter = useTodoStore((s) => s.setFilter)
  const loading = useTodoStore((s) => s.loading)
  const projects = useProjectStore((s) => s.projects)
  const fetchProjects = useProjectStore((s) => s.fetchProjects)
  const addRecentItem = useFavoritesStore((s) => s.addRecentItem)
  const { success, error } = useToast()
  const [createOpen, setCreateOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState({ title: '', description: '', priority: 'medium', project_id: '' as string | null })
  const [validFields, setValidFields] = useState<Record<string, boolean>>({})

  useEffect(() => {
    fetchProjects()
    addRecentItem('kanban', 'project', 'Kanban')
  }, [fetchProjects, addRecentItem])

  useEffect(() => {
    fetchTodos()
  }, [fetchTodos, filterProjectId])

  const handleCreate = async () => {
    if (!form.title.trim()) return
    try {
      setSubmitting(true)
      await createTodo({
        title: form.title,
        description: form.description,
        priority: form.priority,
        project_id: form.project_id || null,
        status: 'backlog',
      })
      success('Todo erfolgreich erstellt!')
      setForm({ title: '', description: '', priority: 'medium', project_id: '' })
      setCreateOpen(false)
    } catch (err) {
      error(`Fehler beim Erstellen: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Select
            value={filterProjectId || '__all__'}
            onValueChange={(v) => setFilter('filterProjectId', v === '__all__' ? null : v)}
          >
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="Alle Projekte" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Alle Projekte</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button onClick={() => setCreateOpen(true)} icon={<Plus className="w-4 h-4" />}>Neues Todo</Button>
      </div>

      <div className="flex-1 overflow-hidden">
        {loading ? (
          <div className="p-6">
            <KanbanSkeleton columns={4} cardsPerColumn={3} />
          </div>
        ) : (
          <KanbanBoard />
        )}
      </div>

      {/* Create Todo Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Neues Todo</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <FormField
              label="Titel"
              error={form.title.trim() === '' && form.title !== '' ? 'Titel erforderlich' : undefined}
              success={validFields.title && form.title.trim() !== ''}
            >
              <Input
                value={form.title}
                onChange={(e) => {
                  setForm({ ...form, title: e.target.value })
                  setValidFields({ ...validFields, title: e.target.value.trim().length > 2 })
                }}
                onBlur={() => setValidFields({ ...validFields, title: form.title.trim().length > 2 })}
                placeholder="Was muss erledigt werden?"
                autoFocus
                aria-invalid={form.title.trim() === '' && form.title !== ''}
              />
            </FormField>
            <FormField
              label="Beschreibung"
              success={validFields.description}
            >
              <Textarea
                value={form.description}
                onChange={(e) => {
                  setForm({ ...form, description: e.target.value })
                  setValidFields({ ...validFields, description: e.target.value.length > 0 })
                }}
                onBlur={() => setValidFields({ ...validFields, description: form.description.length > 0 })}
                placeholder="Details..."
                rows={3}
              />
            </FormField>
            <div className="flex gap-4">
              <div className="flex-1">
                <FormField label="Priorität">
                  <Select value={form.priority} onValueChange={(v) => {
                    setForm({ ...form, priority: v })
                    setValidFields({ ...validFields, priority: true })
                  }}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="high">Hoch</SelectItem>
                      <SelectItem value="medium">Mittel</SelectItem>
                      <SelectItem value="low">Niedrig</SelectItem>
                    </SelectContent>
                  </Select>
                </FormField>
              </div>
              <div className="flex-1">
                <FormField label="Projekt">
                  <Select value={form.project_id || '__none__'} onValueChange={(v) => {
                    setForm({ ...form, project_id: v === '__none__' ? null : v })
                    setValidFields({ ...validFields, project_id: true })
                  }}>
                    <SelectTrigger><SelectValue placeholder="Kein Projekt" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">Kein Projekt</SelectItem>
                      {projects.map((p) => (
                        <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormField>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={submitting}>Abbrechen</Button>
            <Button onClick={handleCreate} disabled={!form.title.trim() || submitting}>
              {submitting ? 'Erstelle...' : 'Erstellen'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
