import {
  addDays,
  ganttTicks,
  xForDate,
  type GanttTick,
  type GanttWindow,
  type GanttZoom,
} from '@/lib/timeline'
import { cn } from '@/lib/utils'

interface Props {
  window: GanttWindow
  zoom: GanttZoom
  pxPerDay: number
  now: Date
}

function tickWidth(tick: GanttTick, zoom: GanttZoom, pxPerDay: number): number {
  if (zoom === 'day') return pxPerDay
  if (zoom === 'week') return 7 * pxPerDay
  const end = new Date(tick.date.getFullYear(), tick.date.getMonth() + 1, 1)
  const days = Math.round((end.getTime() - tick.date.getTime()) / 86_400_000)
  return days * pxPerDay
}

function majorWidth(tick: GanttTick, nextDate: Date | undefined, window: GanttWindow, pxPerDay: number): number {
  const end = nextDate ?? addDays(window.to, 1)
  const startX = xForDate(tick.date, window.from, pxPerDay)
  const endX = xForDate(end, window.from, pxPerDay)
  return Math.max(0, endX - startX)
}

export function GanttAxis({ window, zoom, pxPerDay, now }: Props) {
  const { major, minor } = ganttTicks(window, zoom, now)
  const totalWidth = window.totalDays * pxPerDay

  return (
    <div
      role="row"
      className="sticky top-0 z-20 select-none border-b border-border bg-background"
      style={{ width: totalWidth }}
    >
      <div className="flex h-7 items-center border-b border-border/50">
        {major.map((tick, idx) => {
          const width = majorWidth(tick, major[idx + 1]?.date, window, pxPerDay)
          if (width <= 0) return null
          return (
            <div
              key={tick.date.toISOString()}
              className="border-r border-border/50 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
              style={{ width, minWidth: width }}
            >
              <div className="truncate">{tick.label}</div>
            </div>
          )
        })}
      </div>

      <div className="flex h-7 items-center">
        {minor.map((tick) => {
          const w = tickWidth(tick, zoom, pxPerDay)
          return (
            <div
              key={tick.date.toISOString()}
              className={cn(
                'flex items-center justify-center border-r border-border/40 text-[11px] tabular-nums',
                tick.isWeekend && 'bg-muted/40 text-muted-foreground',
                tick.isToday && 'font-semibold text-brand',
              )}
              style={{ width: w, minWidth: w }}
            >
              {tick.label}
            </div>
          )
        })}
      </div>
    </div>
  )
}
