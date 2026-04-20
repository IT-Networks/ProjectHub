import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Trash2 } from 'lucide-react'
import { useProjectStore } from '@/stores/projectStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { useBulkSelectionStore } from '@/stores/bulkSelectionStore'
import { useToast } from '@/components/shared/Toast'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { EmptyState } from '@/components/shared/EmptyState'
import { CardSkeleton } from '@/components/shared/Skeleton'
import { FormField } from '@/components/shared/FormField'
import { FavoriteButton } from '@/components/shared/FavoriteButton'
import { Checkbox } from '@/components/shared/Checkbox'
import { FilterBar, type FilterConfig } from '@/components/shared/FilterBar'
import { BatchActionsToolbar } from '@/components/shared/BatchActionsToolbar'
import { STATUS_LABELS } from '@/lib/types'
import type { ProjectCreate } from '@/lib/types'

const COLORS = ['#6366f1', '#f43f5e', '#10b981', '#f59e0b', '#3b82f6', '#8b5cf6', '#ec4899', '#14b8a6']

export function ProjectListPage() {
  const { projects, loading, fetchProjects, createProject, deleteProject } = useProjectStore()
  const { success, error } = useToast()
  const addRecentItem = useFavoritesStore((s) => s.addRecentItem)
  const {
    selectedIds,
    isSelectMode,
    toggleItem,
    selectAll,
    deselectAll,
    isSelected,
    getSelectedIds,
    getSelectedCount,
  } = useBulkSelectionStore()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState<ProjectCreate>({ name: '', description: '', color: '#6366f1' })
  const [validFields, setValidFields] = useState<Record<string, boolean>>({})
  const [filters, setFilters] = useState<FilterConfig>({ searchQuery: '' })
  const [showAdvanced, setShowAdvanced] = useState(false)

  useEffect(() => {
    fetchProjects()
    addRecentItem('projekte-page', 'project', 'Projekte')
  }, [fetchProjects, addRecentItem])

  // Filter projects based on search and filters
  const filteredProjects = useMemo(() => {
    return projects.filter((p) => {
      const searchLower = filters.searchQuery.toLowerCase()
      const matchesSearch =
        !searchLower ||
        p.name.toLowerCase().includes(searchLower) ||
        p.description?.toLowerCase().includes(searchLower)

      const matchesStatus = !filters.status || p.status === filters.status

      return matchesSearch && matchesStatus
    })
  }, [projects, filters])

  const handleBatchDelete = async () => {
    const selectedCount = getSelectedCount()
    if (selectedCount === 0) return

    const confirmed = window.confirm(
      `Wirklich ${selectedCount} Projekt(e) löschen?`
    )
    if (!confirmed) return

    try {
      const ids = getSelectedIds()
      for (const id of ids) {
        await deleteProject(id)
      }
      success(`${selectedCount} Projekt(e) gelöscht!`)
      deselectAll()
    } catch (err) {
      error(`Fehler beim Löschen: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
    }
  }

  const handleCreate = async () => {
    if (!form.name.trim()) return
    try {
      setSubmitting(true)
      await createProject(form)
      success('Projekt erfolgreich erstellt!')
      setForm({ name: '', description: '', color: '#6366f1' })
      setDialogOpen(false)
    } catch (err) {
      error(`Fehler beim Erstellen: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-semibold">Projekte</h2>
        <div className="flex gap-2">
          {projects.length > 0 && (
            <Button
              variant={isSelectMode ? 'default' : 'secondary'}
              size="sm"
              onClick={() => {
                if (isSelectMode) {
                  deselectAll()
                } else {
                  selectAll(filteredProjects.map((p) => p.id))
                }
              }}
            >
              {isSelectMode ? 'Abbrechen' : 'Mehrfachauswahl'}
            </Button>
          )}
          <Button onClick={() => setDialogOpen(true)} icon={<Plus className="w-4 h-4" />}>Neues Projekt</Button>
        </div>
      </div>

      {/* Filter and Bulk Actions */}
      {projects.length > 0 && (
        <>
          <FilterBar
            filters={filters}
            onSearchChange={(query) => setFilters({ ...filters, searchQuery: query })}
            onStatusChange={(status) => setFilters({ ...filters, status })}
            onReset={() => setFilters({ searchQuery: '' })}
            statuses={['aktiv', 'pausiert', 'archiviert']}
            showAdvanced={showAdvanced}
            onToggleAdvanced={() => setShowAdvanced(!showAdvanced)}
            className="mb-4"
          />

          {isSelectMode && (
            <BatchActionsToolbar
              selectedCount={getSelectedCount()}
              totalCount={filteredProjects.length}
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
              className="mb-4"
            />
          )}
        </>
      )}

      {loading && projects.length === 0 ? (
        // Show skeleton cards while loading
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <CardSkeleton key={i} lines={2} />
          ))}
        </div>
      ) : filteredProjects.length === 0 && projects.length === 0 ? (
        // Show empty state when no projects
        <EmptyState
          icon="📁"
          title="Keine Projekte vorhanden"
          description="Erstelle dein erstes Projekt, um zu beginnen. Organisiere deine Arbeit, verwalte Quellen und verfolge Fortschritt."
          action={<Button onClick={() => setDialogOpen(true)} icon={<Plus className="w-4 h-4" />}>Neues Projekt</Button>}
        />
      ) : filteredProjects.length === 0 ? (
        // Show empty state when filtered to nothing
        <EmptyState
          icon="🔍"
          title="Keine Ergebnisse"
          description="Keine Projekte entsprechen deinen Filterkriterien. Versuche andere Suchbegriffe oder Filter."
          action={<Button variant="secondary" onClick={() => setFilters({ searchQuery: '' })}>Filter zurücksetzen</Button>}
        />
      ) : (
        // Show project cards
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filteredProjects.map((p) => (
            <div key={p.id}>
              <Card
                className={`group cursor-pointer p-5 transition-colors ${
                  isSelected(p.id) ? 'border-primary bg-primary/5' : 'hover:bg-accent/50'
                }`}
                onClick={(e) => {
                  if (isSelectMode && !(e.target as HTMLElement).closest('a, button')) {
                    toggleItem(p.id)
                  }
                }}
              >
                <div className="mb-3 flex items-center gap-3">
                  {isSelectMode && (
                    <Checkbox
                      checked={isSelected(p.id)}
                      onChange={(checked) => {
                        if (checked) {
                          toggleItem(p.id)
                        } else {
                          toggleItem(p.id)
                        }
                      }}
                      className="flex-shrink-0"
                      ariaLabel={`Select ${p.name}`}
                    />
                  )}
                  <span
                    className="h-3 w-3 rounded-full"
                    style={{ backgroundColor: p.color }}
                  />
                  <Link
                    to={`/projekte/${p.id}`}
                    onClick={() => addRecentItem(p.id, 'project', p.name)}
                    className="flex-1 flex items-center gap-3"
                  >
                    <span className="font-medium flex-1">{p.name}</span>
                  </Link>
                  <FavoriteButton
                    id={p.id}
                    type="project"
                    title={p.name}
                    size="sm"
                    className={`transition-opacity ${
                      isSelectMode ? 'opacity-0' : 'opacity-0 group-hover:opacity-100'
                    }`}
                  />
                  <Badge variant="secondary" className="text-xs">
                    {STATUS_LABELS[p.status] || p.status}
                  </Badge>
                </div>
                {p.description && (
                  <p className="mb-3 line-clamp-2 text-sm text-muted-foreground">
                    {p.description}
                  </p>
                )}
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  <span>{p.todo_open} offene Todos</span>
                  <span>{p.source_count} Quellen</span>
                </div>
                {p.tags.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {p.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-xs">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                )}
              </Card>
            </div>
          ))}
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Neues Projekt</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <FormField
              label="Name"
              error={form.name.trim() === '' && form.name !== '' ? 'Name erforderlich' : undefined}
              success={validFields.name && form.name.trim() !== ''}
            >
              <Input
                value={form.name}
                onChange={(e) => {
                  setForm({ ...form, name: e.target.value })
                  setValidFields({ ...validFields, name: e.target.value.trim().length > 2 })
                }}
                onBlur={() => setValidFields({ ...validFields, name: form.name.trim().length > 2 })}
                placeholder="Projektname"
                autoFocus
                aria-invalid={form.name.trim() === '' && form.name !== ''}
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
                placeholder="Kurze Beschreibung..."
                rows={3}
              />
            </FormField>
            <div>
              <label className="mb-2 block text-sm font-medium">Farbe</label>
              <div className="flex gap-2">
                {COLORS.map((c) => (
                  <button
                    key={c}
                    className="h-7 w-7 rounded-full border-2 transition-transform hover:scale-110"
                    style={{
                      backgroundColor: c,
                      borderColor: form.color === c ? 'white' : 'transparent',
                    }}
                    onClick={() => setForm({ ...form, color: c })}
                  />
                ))}
              </div>
            </div>
            <FormField label="Status">
              <Select
                value={form.status || 'aktiv'}
                onValueChange={(v) => {
                  setForm({ ...form, status: v })
                  setValidFields({ ...validFields, status: true })
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="aktiv">Aktiv</SelectItem>
                  <SelectItem value="pausiert">Pausiert</SelectItem>
                  <SelectItem value="archiviert">Archiviert</SelectItem>
                </SelectContent>
              </Select>
            </FormField>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={submitting}>
              Abbrechen
            </Button>
            <Button onClick={handleCreate} disabled={!form.name.trim() || submitting}>
              {submitting ? 'Erstelle...' : 'Erstellen'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
