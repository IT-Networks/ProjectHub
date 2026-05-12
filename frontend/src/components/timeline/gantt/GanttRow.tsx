import { useMemo } from 'react'
import { GanttBar } from './GanttBar'
import {
  addDays,
  ganttTicks,
  type GanttWindow,
  type GanttZoom,
  type Swimlane,
  type TimelineItem,
} from '@/lib/timeline'
import { cn } from '@/lib/utils'

interface Props {
  lane: Swimlane
  window: GanttWindow
  zoom: GanttZoom
  pxPerDay: number
  now: Date
  onItemClick?: (item: TimelineItem) => void
}

const ROW_HEIGHT = 52

export function GanttRow({ lane, window, zoom, pxPerDay, now, onItemClick }: Props) {
  const totalWidth = window.totalDays * pxPerDay

  const weekendBands = useMemo(() => {
    if (zoom !== 'day') return []
    const bands: { x: number; width: number }[] = []
    let cursor = new Date(window.from)
    while (cursor <= window.to) {
      const dow = cursor.getDay()
      if (dow === 0 || dow === 6) {
        const idx = Math.round((cursor.getTime() - window.from.getTime()) / 86_400_000)
        bands.push({ x: idx * pxPerDay, width: pxPerDay })
      }
      cursor = addDays(cursor, 1)
    }
    return bands
  }, [zoom, window, pxPerDay])

  const tickLines = useMemo(() => {
    const { minor } = ganttTicks(window, zoom, now)
    const lines: number[] = []
    for (let i = 0; i < minor.length; i++) {
      const tick = minor[i]
      const diff = Math.round((tick.date.getTime() - window.from.getTime()) / 86_400_000)
      lines.push(diff * pxPerDay)
    }
    return lines
  }, [window, zoom, pxPerDay, now])

  return (
    <div
      role="row"
      className="relative border-b border-border"
      style={{ width: totalWidth, height: ROW_HEIGHT }}
    >
      {weekendBands.map((b, idx) => (
        <div
          key={idx}
          aria-hidden
          className="absolute top-0 bg-muted/30"
          style={{ left: b.x, width: b.width, height: ROW_HEIGHT }}
        />
      ))}

      {tickLines.map((x, idx) => (
        <div
          key={idx}
          aria-hidden
          className={cn('absolute top-0 w-px bg-border/40', idx === 0 && 'opacity-0')}
          style={{ left: x, height: ROW_HEIGHT }}
        />
      ))}

      {lane.items.map((item) => (
        <GanttBar
          key={`${item.kind}-${item.id}`}
          item={item}
          window={window}
          pxPerDay={pxPerDay}
          onClick={onItemClick}
        />
      ))}
    </div>
  )
}
