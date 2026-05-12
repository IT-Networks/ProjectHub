import { useEffect, useMemo, useRef } from 'react'
import { GanttAxis } from './gantt/GanttAxis'
import { GanttRow } from './gantt/GanttRow'
import { TodayLine } from './gantt/TodayLine'
import { EmptyState } from '@/components/shared/EmptyState'
import {
  PX_PER_DAY,
  ganttWindow,
  groupByLane,
  startOfDay,
  xForDate,
  type GanttLane,
  type GanttZoom,
  type TimelineItem,
} from '@/lib/timeline'
import { cn } from '@/lib/utils'

const LANE_HEADER_W = 180
const ROW_HEIGHT = 52
const AXIS_HEIGHT = 56

interface Props {
  cursor: Date
  zoom: GanttZoom
  lane: GanttLane
  items: readonly TimelineItem[]
  now?: Date
  onItemClick?: (item: TimelineItem) => void
}

export function GanttView({ cursor, zoom, lane, items, now = new Date(), onItemClick }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const window = useMemo(() => ganttWindow(cursor, zoom), [cursor, zoom])
  const pxPerDay = PX_PER_DAY[zoom]
  const swimlanes = useMemo(() => groupByLane(items, lane), [items, lane])

  const todayX = useMemo(() => {
    const today = startOfDay(now)
    if (today < window.from || today > window.to) return null
    return xForDate(today, window.from, pxPerDay)
  }, [now, window, pxPerDay])

  const totalWidth = window.totalDays * pxPerDay
  const totalHeight = AXIS_HEIGHT + swimlanes.length * ROW_HEIGHT

  useEffect(() => {
    const el = scrollRef.current
    if (!el || todayX === null) return
    const visible = el.clientWidth - LANE_HEADER_W
    const target = Math.max(0, todayX - visible / 3)
    el.scrollTo({ left: target, behavior: 'smooth' })
  }, [todayX, zoom, lane])

  if (swimlanes.length === 0) {
    return (
      <EmptyState
        icon="📊"
        title="Keine Items im Fenster"
        description="Verschiebe den Cursor oder setze Filter zurück, um Items in diesem Zeitraum zu sehen."
        size="spacious"
      />
    )
  }

  return (
    <div
      role="grid"
      aria-label="Gantt-Ansicht"
      className="overflow-hidden rounded-lg border border-border bg-card"
    >
      <div className="flex">
        <div
          className="sticky left-0 z-30 shrink-0 border-r border-border bg-card"
          style={{ width: LANE_HEADER_W }}
        >
          <div
            className="border-b border-border px-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
            style={{ height: AXIS_HEIGHT, lineHeight: `${AXIS_HEIGHT}px` }}
          >
            {lane === 'project' ? 'Projekt' : lane === 'priority' ? 'Priorität' : 'Zuständig'}
          </div>
          {swimlanes.map((sl) => (
            <div
              key={sl.id}
              role="rowheader"
              className="flex items-center gap-2 border-b border-border px-3 text-xs"
              style={{ height: ROW_HEIGHT }}
            >
              {sl.color && (
                <span
                  aria-hidden
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: sl.color }}
                />
              )}
              <span className="truncate font-medium">{sl.label}</span>
              <span className="ml-auto shrink-0 rounded-full bg-muted px-1.5 py-0 text-[10px] tabular-nums text-muted-foreground">
                {sl.items.length}
              </span>
            </div>
          ))}
        </div>

        <div
          ref={scrollRef}
          className="relative flex-1 overflow-x-auto"
          style={{ maxWidth: `calc(100vw - 15rem - ${LANE_HEADER_W}px - 3rem)` }}
        >
          <div className="relative" style={{ width: totalWidth, height: totalHeight }}>
            <GanttAxis window={window} zoom={zoom} pxPerDay={pxPerDay} now={now} />

            <div>
              {swimlanes.map((sl) => (
                <GanttRow
                  key={sl.id}
                  lane={sl}
                  window={window}
                  zoom={zoom}
                  pxPerDay={pxPerDay}
                  now={now}
                  onItemClick={onItemClick}
                />
              ))}
            </div>

            {todayX !== null && <TodayLine left={todayX} height={totalHeight} />}
          </div>
        </div>
      </div>

      <div
        className={cn(
          'border-t border-border bg-muted/20 px-3 py-1.5 text-[11px] text-muted-foreground',
          'flex items-center justify-between',
        )}
      >
        <span>
          {swimlanes.length} Lanes · {items.length} Items im Fenster
        </span>
        <span>
          {window.from.toLocaleDateString('de-DE', { day: '2-digit', month: 'short' })} –{' '}
          {window.to.toLocaleDateString('de-DE', { day: '2-digit', month: 'short', year: 'numeric' })}
        </span>
      </div>
    </div>
  )
}
