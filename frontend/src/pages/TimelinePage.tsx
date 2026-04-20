import { useEffect, useMemo, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from 'recharts'
import { useTodoStore } from '@/stores/todoStore'
import { useProjectStore } from '@/stores/projectStore'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { EmptyState } from '@/components/shared/EmptyState'
import { STATUS_LABELS, PRIORITY_LABELS } from '@/lib/types'
import type { Todo } from '@/lib/types'

const STATUS_COLORS: Record<string, string> = {
  backlog: '#6b7280',
  in_progress: '#3b82f6',
  review: '#f59e0b',
  done: '#10b981',
}

const PRIORITY_SORT: Record<string, number> = { high: 0, medium: 1, low: 2 }

interface GanttBar {
  name: string
  todoId: string
  start: number
  duration: number
  status: string
  priority: string
  projectName: string
  deadline: string
  createdAt: string
}

export function TimelinePage() {
  const fetchTodos = useTodoStore((s) => s.fetchTodos)
  const todos = useTodoStore((s) => s.todos)
  const projects = useProjectStore((s) => s.projects)
  const [filterProject, setFilterProject] = useState<string | null>(null)

  useEffect(() => { fetchTodos() }, [fetchTodos])

  const projectMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const p of projects) map[p.id] = p.name
    return map
  }, [projects])

  // Build Gantt data from todos with deadlines
  const ganttData = useMemo(() => {
    const now = new Date()
    const todosWithDates = todos
      .filter((t) => t.deadline && t.status !== 'done')
      .filter((t) => !filterProject || t.project_id === filterProject)
      .sort((a, b) => {
        // Sort by deadline, then priority
        const da = new Date(a.deadline!).getTime()
        const db = new Date(b.deadline!).getTime()
        if (da !== db) return da - db
        return (PRIORITY_SORT[a.priority] ?? 1) - (PRIORITY_SORT[b.priority] ?? 1)
      })

    const minDate = new Date(Math.min(now.getTime(), ...todosWithDates.map((t) => new Date(t.created_at).getTime())))
    const dayZero = new Date(minDate.getFullYear(), minDate.getMonth(), minDate.getDate()).getTime()

    return todosWithDates.map((t): GanttBar => {
      const created = new Date(t.created_at).getTime()
      const deadline = new Date(t.deadline!).getTime()
      const startDay = Math.max(0, Math.floor((created - dayZero) / (1000 * 60 * 60 * 24)))
      const durationDays = Math.max(1, Math.ceil((deadline - created) / (1000 * 60 * 60 * 24)))

      return {
        name: t.title.length > 35 ? t.title.slice(0, 35) + '...' : t.title,
        todoId: t.id,
        start: startDay,
        duration: durationDays,
        status: t.status,
        priority: t.priority,
        projectName: t.project_id ? (projectMap[t.project_id] || '') : 'Standalone',
        deadline: t.deadline!,
        createdAt: t.created_at,
      }
    })
  }, [todos, filterProject, projectMap])

  // Done todos stats
  const doneCount = todos.filter((t) => t.status === 'done').length
  const openCount = todos.filter((t) => t.status !== 'done').length
  const overdueCount = todos.filter((t) => t.deadline && t.status !== 'done' && new Date(t.deadline) < new Date()).length

  const CustomTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null
    const d = payload[0].payload as GanttBar
    return (
      <div className="rounded-lg border border-border bg-popover px-3 py-2 text-sm shadow-lg">
        <p className="font-medium">{d.name}</p>
        <p className="text-xs text-muted-foreground">{d.projectName}</p>
        <div className="mt-1 flex gap-2 text-xs">
          <Badge variant="outline" className="text-xs">{STATUS_LABELS[d.status]}</Badge>
          <Badge variant="outline" className="text-xs">{PRIORITY_LABELS[d.priority]}</Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Frist: {new Date(d.deadline).toLocaleDateString('de-DE')}
        </p>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Select value={filterProject || '__all__'} onValueChange={(v) => setFilterProject(v === '__all__' ? null : v)}>
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
        <div className="flex gap-4 text-sm">
          <span className="text-muted-foreground">{openCount} offen</span>
          <span className="text-green-500">{doneCount} erledigt</span>
          {overdueCount > 0 && <span className="text-red-400">{overdueCount} überfällig</span>}
        </div>
      </div>

      {ganttData.length === 0 ? (
        <EmptyState
          icon="📅"
          title="Keine Todos mit Fristen"
          description="Erstelle Todos mit einer Frist oder setze eine Frist für bestehende Todos, um sie hier als Timeline zu sehen."
          size="spacious"
        />
      ) : (
        <div style={{ height: Math.max(300, ganttData.length * 40 + 60) }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={ganttData}
              layout="vertical"
              margin={{ top: 10, right: 30, left: 200, bottom: 10 }}
              barSize={20}
            >
              <XAxis
                type="number"
                domain={[0, 'auto']}
                tickFormatter={(v) => `Tag ${v}`}
                stroke="hsl(var(--muted-foreground))"
                fontSize={12}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={190}
                tick={{ fontSize: 12, fill: 'hsl(var(--foreground))' }}
              />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="start" stackId="gantt" fill="transparent" />
              <Bar dataKey="duration" stackId="gantt" radius={[4, 4, 4, 4]}>
                {ganttData.map((entry, idx) => (
                  <Cell key={idx} fill={STATUS_COLORS[entry.status] || '#6b7280'} fillOpacity={0.8} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Legend */}
      <div className="mt-4 flex items-center gap-6 text-xs text-muted-foreground">
        {Object.entries(STATUS_COLORS).map(([status, color]) => (
          <div key={status} className="flex items-center gap-1.5">
            <span className="h-3 w-3 rounded" style={{ backgroundColor: color, opacity: 0.8 }} />
            {STATUS_LABELS[status]}
          </div>
        ))}
      </div>
    </div>
  )
}
