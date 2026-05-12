import { useEffect, useMemo, Suspense, lazy } from 'react'
import { useTodoStore } from '@/stores/todoStore'
import { useNoteStore } from '@/stores/noteStore'
import { useProjectStore } from '@/stores/projectStore'
import { useTimelineStore } from '@/stores/timelineStore'
import { useTimelineUrlState } from '@/hooks/useTimelineUrlState'
import { useViewTransitionNavigate } from '@/hooks/useViewTransition'
import {
  filterItems,
  formatCursorLabel,
  groupByBucket,
  toTimelineItems,
  type TimelineItem,
} from '@/lib/timeline'
import { TimelineHeader } from '@/components/timeline/shared/TimelineHeader'
import { ScheduleView } from '@/components/timeline/ScheduleView'

const CalendarView = lazy(() =>
  import('@/components/timeline/CalendarView').then((m) => ({ default: m.CalendarView })),
)

const GanttView = lazy(() =>
  import('@/components/timeline/GanttView').then((m) => ({ default: m.GanttView })),
)

export function TimelinePage() {
  useTimelineUrlState()

  const todos = useTodoStore((s) => s.todos)
  const fetchTodos = useTodoStore((s) => s.fetchTodos)
  const notes = useNoteStore((s) => s.notes)
  const fetchNotes = useNoteStore((s) => s.fetchNotes)
  const projects = useProjectStore((s) => s.projects)

  const view = useTimelineStore((s) => s.view)
  const cursor = useTimelineStore((s) => s.cursor)
  const zoom = useTimelineStore((s) => s.zoom)
  const lane = useTimelineStore((s) => s.lane)
  const filter = useTimelineStore((s) => s.filter)
  const setView = useTimelineStore((s) => s.setView)
  const setZoom = useTimelineStore((s) => s.setZoom)
  const setLane = useTimelineStore((s) => s.setLane)
  const setFilter = useTimelineStore((s) => s.setFilter)
  const resetFilter = useTimelineStore((s) => s.resetFilter)
  const shiftCursor = useTimelineStore((s) => s.shiftCursor)
  const goToday = useTimelineStore((s) => s.goToday)

  const navigate = useViewTransitionNavigate()

  useEffect(() => { fetchTodos() }, [fetchTodos])
  useEffect(() => { fetchNotes() }, [fetchNotes])

  const allItems = useMemo(
    () => toTimelineItems({ todos, notes, projects }),
    [todos, notes, projects],
  )

  const filteredItems = useMemo(
    () => filterItems(allItems, filter),
    [allItems, filter],
  )

  const now = useMemo(() => new Date(), [])
  const cursorDate = useMemo(() => {
    const d = new Date(cursor)
    return isNaN(d.getTime()) ? now : d
  }, [cursor, now])

  const buckets = useMemo(
    () => groupByBucket(filteredItems, { now, showCompleted: filter.showCompleted }),
    [filteredItems, filter.showCompleted, now],
  )

  const counts = useMemo(() => {
    const overdue = allItems.filter((i) => !i.completed && new Date(i.at) < now).length
    const completed = allItems.filter((i) => i.completed).length
    return { total: allItems.length, overdue, completed }
  }, [allItems, now])

  const handleItemClick = (item: TimelineItem) => {
    if (item.project_id) navigate(`/projekte/${item.project_id}`)
    else if (item.kind === 'todo') navigate('/kanban')
  }

  const cursorLabel = formatCursorLabel(cursorDate, view)
  const showCursorNav = view !== 'schedule'

  return (
    <div className="p-6">
      <TimelineHeader
        view={view}
        cursorLabel={cursorLabel}
        filter={filter}
        projects={projects}
        counts={counts}
        showCursorNav={showCursorNav}
        zoom={zoom}
        lane={lane}
        onViewChange={setView}
        onShiftCursor={shiftCursor}
        onGoToday={goToday}
        onFilterChange={setFilter}
        onResetFilter={resetFilter}
        onZoomChange={setZoom}
        onLaneChange={setLane}
      />

      {view === 'schedule' && (
        <ScheduleView buckets={buckets} now={now} onItemClick={handleItemClick} />
      )}

      {view === 'calendar' && (
        <Suspense fallback={<div className="py-12 text-center text-sm text-muted-foreground">Lädt…</div>}>
          <CalendarView
            cursor={cursorDate}
            items={filteredItems}
            now={now}
            onItemClick={handleItemClick}
          />
        </Suspense>
      )}

      {view === 'gantt' && (
        <Suspense fallback={<div className="py-12 text-center text-sm text-muted-foreground">Lädt…</div>}>
          <GanttView
            cursor={cursorDate}
            zoom={zoom}
            lane={lane}
            items={filteredItems}
            now={now}
            onItemClick={handleItemClick}
          />
        </Suspense>
      )}
    </div>
  )
}
