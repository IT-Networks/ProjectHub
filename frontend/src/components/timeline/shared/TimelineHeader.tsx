import { useEffect, useRef } from 'react'
import { ChevronLeft, ChevronRight, Search, X, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ViewToggle } from './ViewToggle'
import type { ProjectListItem } from '@/lib/types'
import type { TimelineFilter, GanttLane, GanttZoom } from '@/lib/timeline'
import type { TimelineView } from '@/stores/timelineStore'
import { cn } from '@/lib/utils'

interface Props {
  view: TimelineView
  cursorLabel: string
  filter: TimelineFilter
  projects: readonly ProjectListItem[]
  counts: { total: number; overdue: number; completed: number }
  showCursorNav: boolean
  zoom?: GanttZoom
  lane?: GanttLane
  onViewChange: (v: TimelineView) => void
  onShiftCursor: (units: number) => void
  onGoToday: () => void
  onFilterChange: (patch: Partial<TimelineFilter>) => void
  onResetFilter: () => void
  onZoomChange?: (z: GanttZoom) => void
  onLaneChange?: (l: GanttLane) => void
}

export function TimelineHeader({
  view,
  cursorLabel,
  filter,
  projects,
  counts,
  showCursorNav,
  zoom,
  lane,
  onViewChange,
  onShiftCursor,
  onGoToday,
  onFilterChange,
  onResetFilter,
  onZoomChange,
  onLaneChange,
}: Props) {
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      const isFormEl =
        tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement | null)?.isContentEditable
      if (isFormEl) return
      if (e.ctrlKey || e.metaKey || e.altKey) return

      if (e.key === '1') { e.preventDefault(); onViewChange('schedule') }
      else if (e.key === '2') { e.preventDefault(); onViewChange('calendar') }
      else if (e.key === '3') { e.preventDefault(); onViewChange('gantt') }
      else if (e.key === '.') { e.preventDefault(); onGoToday() }
      else if (e.key === 'h' && showCursorNav) { e.preventDefault(); onShiftCursor(-1) }
      else if (e.key === 'l' && showCursorNav) { e.preventDefault(); onShiftCursor(1) }
      else if (e.key === '/') { e.preventDefault(); searchRef.current?.focus() }
      else if (e.key === 'c') { e.preventDefault(); onFilterChange({ showCompleted: !filter.showCompleted }) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [filter.showCompleted, onFilterChange, onGoToday, onShiftCursor, onViewChange, showCursorNav])

  const hasFilters =
    filter.projectId !== null ||
    filter.priority !== null ||
    filter.kind !== null ||
    filter.tag !== null ||
    filter.q !== ''

  return (
    <div className="sticky top-0 z-20 -mx-6 mb-4 border-b border-border bg-background/80 px-6 py-3 backdrop-blur">
      <div className="flex flex-wrap items-center gap-3">
        <ViewToggle value={view} onChange={onViewChange} />

        {showCursorNav && (
          <div className="flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={() => onShiftCursor(-1)} aria-label="Zurück" className="h-8 w-8 p-0">
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="min-w-[140px] text-center text-sm font-medium tabular-nums">
              {cursorLabel}
            </div>
            <Button size="sm" variant="ghost" onClick={() => onShiftCursor(1)} aria-label="Vor" className="h-8 w-8 p-0">
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button size="sm" variant="outline" onClick={onGoToday} className="ml-1 h-8 px-2 text-xs">
              Heute
            </Button>
          </div>
        )}

        {view === 'gantt' && zoom && onZoomChange && (
          <Select value={zoom} onValueChange={(v) => onZoomChange(v as GanttZoom)}>
            <SelectTrigger className="h-8 w-[110px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="day">Tag</SelectItem>
              <SelectItem value="week">Woche</SelectItem>
              <SelectItem value="month">Monat</SelectItem>
            </SelectContent>
          </Select>
        )}

        {view === 'gantt' && lane && onLaneChange && (
          <Select value={lane} onValueChange={(v) => onLaneChange(v as GanttLane)}>
            <SelectTrigger className="h-8 w-[140px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="project">Nach Projekt</SelectItem>
              <SelectItem value="priority">Nach Priorität</SelectItem>
              <SelectItem value="assignee">Nach Person</SelectItem>
            </SelectContent>
          </Select>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              ref={searchRef}
              type="text"
              value={filter.q}
              onChange={(e) => onFilterChange({ q: e.target.value })}
              placeholder="Suche"
              aria-label="Timeline-Suche"
              className={cn(
                'h-8 w-44 rounded-md border border-input bg-background pl-7 pr-6 text-xs outline-none transition-colors',
                'focus:border-brand focus:ring-2 focus:ring-brand/20',
              )}
            />
            {filter.q && (
              <button
                type="button"
                onClick={() => onFilterChange({ q: '' })}
                aria-label="Suche leeren"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          <Select
            value={filter.projectId ?? '__all__'}
            onValueChange={(v) => onFilterChange({ projectId: v === '__all__' ? null : v })}
          >
            <SelectTrigger className="h-8 w-[160px] text-xs">
              <SelectValue placeholder="Alle Projekte" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Alle Projekte</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filter.priority ?? '__any__'}
            onValueChange={(v) => onFilterChange({ priority: v === '__any__' ? null : (v as typeof filter.priority) })}
          >
            <SelectTrigger className="h-8 w-[130px] text-xs">
              <SelectValue placeholder="Priorität" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__any__">Alle Prio</SelectItem>
              <SelectItem value="high">Hoch</SelectItem>
              <SelectItem value="medium">Mittel</SelectItem>
              <SelectItem value="low">Niedrig</SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={filter.kind ?? '__both__'}
            onValueChange={(v) => onFilterChange({ kind: v === '__both__' ? null : (v as typeof filter.kind) })}
          >
            <SelectTrigger className="h-8 w-[110px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__both__">Alle Typen</SelectItem>
              <SelectItem value="todo">Todos</SelectItem>
              <SelectItem value="note">Notizen</SelectItem>
            </SelectContent>
          </Select>

          <label className="flex cursor-pointer items-center gap-1.5 rounded-md border border-input px-2 py-1 text-xs">
            <input
              type="checkbox"
              checked={filter.showCompleted}
              onChange={(e) => onFilterChange({ showCompleted: e.target.checked })}
              className="accent-brand"
            />
            Erledigte
          </label>

          {hasFilters && (
            <Button size="sm" variant="ghost" onClick={onResetFilter} className="h-8 gap-1 px-2 text-xs">
              <RotateCcw className="h-3 w-3" />
              Reset
            </Button>
          )}
        </div>
      </div>

      <div className="mt-2 flex items-center gap-4 text-xs">
        <span className="text-muted-foreground tabular-nums">{counts.total} gesamt</span>
        {counts.overdue > 0 && (
          <span className="text-red-500 tabular-nums">{counts.overdue} überfällig</span>
        )}
        {counts.completed > 0 && (
          <span className="text-emerald-500 tabular-nums">{counts.completed} erledigt</span>
        )}
      </div>
    </div>
  )
}
