import { useEffect, useState } from 'react'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, rectSortingStrategy } from '@dnd-kit/sortable'
import { useDashboardStore } from '@/stores/dashboardStore'
import { useProjectStore } from '@/stores/projectStore'
import { WidgetWrapper } from './WidgetWrapper'
import { TodoCountWidget } from './TodoCountWidget'
import { ProjectStatusWidget } from './ProjectStatusWidget'
import { DeadlineCalendarWidget } from './DeadlineCalendarWidget'
import { BuildStatusWidget } from './BuildStatusWidget'
import { PRListWidget } from './PRListWidget'
import { ActivityWidget } from './ActivityWidget'
import { KnowledgeWidget } from './KnowledgeWidget'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { WidgetConfig } from '@/lib/types'

const WIDGET_TYPES: Record<string, { label: string; defaultWidth: number; defaultHeight: number }> = {
  todo_count: { label: 'Todo-Übersicht', defaultWidth: 1, defaultHeight: 1 },
  project_status: { label: 'Projekt-Status', defaultWidth: 1, defaultHeight: 1 },
  deadline_calendar: { label: 'Fristen-Kalender', defaultWidth: 2, defaultHeight: 1 },
  build_status: { label: 'Jenkins Builds', defaultWidth: 1, defaultHeight: 1 },
  pr_list: { label: 'GitHub PRs', defaultWidth: 1, defaultHeight: 1 },
  activity: { label: 'Aktivitäten', defaultWidth: 1, defaultHeight: 2 },
  knowledge: { label: 'Projektwissen', defaultWidth: 1, defaultHeight: 1 },
}

function renderWidget(widget: WidgetConfig) {
  switch (widget.widget_type) {
    case 'todo_count':
      return <TodoCountWidget config={widget.config} />
    case 'project_status':
      return <ProjectStatusWidget config={widget.config} />
    case 'deadline_calendar':
      return <DeadlineCalendarWidget />
    case 'build_status':
      return <BuildStatusWidget config={widget.config} />
    case 'pr_list':
      return <PRListWidget config={widget.config} />
    case 'activity':
      return <ActivityWidget config={widget.config} />
    case 'knowledge':
      return <KnowledgeWidget config={widget.config} />
    default:
      return <p className="text-sm text-muted-foreground">Widget: {widget.widget_type}</p>
  }
}

function widgetTitle(widget: WidgetConfig): string {
  return WIDGET_TYPES[widget.widget_type]?.label || widget.widget_type
}

export function WidgetGrid() {
  const { widgets, fetchDashboard, addWidget, removeWidget } = useDashboardStore()
  const { projects } = useProjectStore()
  const [addOpen, setAddOpen] = useState(false)
  const [newType, setNewType] = useState('todo_count')
  const [newProjectId, setNewProjectId] = useState('')

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  useEffect(() => {
    fetchDashboard()
  }, [fetchDashboard])

  const handleAdd = async () => {
    const typeInfo = WIDGET_TYPES[newType]
    const config: Record<string, unknown> = {}
    if (newProjectId) config.project_id = newProjectId

    // Place in next available row
    const maxRow = widgets.reduce((max, w) => Math.max(max, w.grid_row + w.grid_height), 0)

    await addWidget({
      widget_type: newType,
      grid_col: 0,
      grid_row: maxRow,
      grid_width: typeInfo?.defaultWidth || 1,
      grid_height: typeInfo?.defaultHeight || 1,
      config,
    })
    setAddOpen(false)
    setNewType('todo_count')
    setNewProjectId('')
  }

  const handleDragEnd = (_event: DragEndEvent) => {
    // Simple reorder — could implement full grid repositioning later
  }

  return (
    <div>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={widgets.map((w) => w.id)} strategy={rectSortingStrategy}>
          <div className="grid grid-cols-4 gap-4 auto-rows-[minmax(160px,auto)]">
            {widgets.filter((w) => w.is_visible).map((widget) => (
              <WidgetWrapper
                key={widget.id}
                widget={widget}
                title={widgetTitle(widget)}
                onRemove={() => removeWidget(widget.id)}
              >
                {renderWidget(widget)}
              </WidgetWrapper>
            ))}
          </div>
        </SortableContext>
      </DndContext>

      <div className="mt-4">
        <Button variant="outline" onClick={() => setAddOpen(true)}>
          + Widget hinzufügen
        </Button>
      </div>

      {/* Add Widget Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Widget hinzufügen</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="mb-1 block text-sm font-medium">Widget-Typ</label>
              <Select value={newType} onValueChange={setNewType}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(WIDGET_TYPES).map(([key, val]) => (
                    <SelectItem key={key} value={key}>{val.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {(newType === 'todo_count' || newType === 'project_status') && (
              <div>
                <label className="mb-1 block text-sm font-medium">Projekt (optional)</label>
                <Select value={newProjectId || '__all__'} onValueChange={(v) => setNewProjectId(v === '__all__' ? '' : v)}>
                  <SelectTrigger><SelectValue placeholder="Alle Projekte" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">Alle Projekte</SelectItem>
                    {projects.map((p) => (
                      <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>Abbrechen</Button>
            <Button onClick={handleAdd}>Hinzufügen</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
