import { useMemo, useState, useCallback, useEffect } from 'react'
import { DaySheet } from './DaySheet'
import { ItemDot } from './shared/ItemDot'
import {
  daysInMonthGrid,
  groupByDay,
  isSameDay,
  isSameMonth,
  startOfDay,
  addDays,
  type TimelineItem,
} from '@/lib/timeline'
import { cn } from '@/lib/utils'

interface Props {
  cursor: Date
  items: readonly TimelineItem[]
  now?: Date
  onItemClick?: (item: TimelineItem) => void
}

const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'] as const
const MAX_PREVIEW = 3

function dayKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${dd}`
}

export function CalendarView({ cursor, items, now = new Date(), onItemClick }: Props) {
  const days = useMemo(() => daysInMonthGrid(cursor), [cursor])

  const itemsByDay = useMemo(() => {
    const from = days[0]
    const to = days[days.length - 1]
    return groupByDay(items, { from, to })
  }, [items, days])

  const [selectedDate, setSelectedDate] = useState<Date | null>(null)
  const [focusDate, setFocusDate] = useState<Date>(startOfDay(now))

  const todayStart = useMemo(() => startOfDay(now), [now])

  const openDay = useCallback((d: Date) => {
    setSelectedDate(d)
    setFocusDate(d)
  }, [])

  const closeDay = useCallback(() => setSelectedDate(null), [])

  const selectedItems = useMemo(() => {
    if (!selectedDate) return []
    return itemsByDay.get(dayKey(selectedDate)) ?? []
  }, [selectedDate, itemsByDay])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      const isFormEl =
        tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement | null)?.isContentEditable
      if (isFormEl) return
      if (e.ctrlKey || e.metaKey || e.altKey) return

      if (selectedDate) return

      let delta = 0
      if (e.key === 'ArrowLeft') delta = -1
      else if (e.key === 'ArrowRight') delta = 1
      else if (e.key === 'ArrowUp') delta = -7
      else if (e.key === 'ArrowDown') delta = 7

      if (delta !== 0) {
        e.preventDefault()
        setFocusDate((prev) => addDays(prev, delta))
      } else if (e.key === 'Enter' || e.key === ' ') {
        const btn = document.querySelector<HTMLButtonElement>(
          `[data-calendar-day="${dayKey(focusDate)}"]`,
        )
        if (btn) {
          e.preventDefault()
          btn.focus()
          btn.click()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [focusDate, selectedDate])

  return (
    <>
      <div
        role="grid"
        aria-label="Kalender"
        aria-readonly
        className="overflow-hidden rounded-lg border border-border bg-card"
      >
        <div role="row" className="grid grid-cols-7 border-b border-border bg-muted/30">
          {WEEKDAYS.map((w) => (
            <div
              key={w}
              role="columnheader"
              className="px-2 py-1.5 text-center text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
            >
              {w}
            </div>
          ))}
        </div>

        {Array.from({ length: 6 }).map((_, weekIdx) => (
          <div key={weekIdx} role="row" className="grid grid-cols-7 border-b border-border last:border-b-0">
            {days.slice(weekIdx * 7, weekIdx * 7 + 7).map((d) => {
              const key = dayKey(d)
              const dayItems = itemsByDay.get(key) ?? []
              const preview = dayItems.slice(0, MAX_PREVIEW)
              const extra = dayItems.length - preview.length
              const inMonth = isSameMonth(d, cursor)
              const isToday = isSameDay(d, todayStart)
              const isFocus = isSameDay(d, focusDate)

              return (
                <button
                  key={key}
                  role="gridcell"
                  type="button"
                  data-calendar-day={key}
                  onClick={() => openDay(d)}
                  onFocus={() => setFocusDate(d)}
                  tabIndex={isFocus ? 0 : -1}
                  aria-label={`${d.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' })}${dayItems.length ? `, ${dayItems.length} Einträge` : ''}`}
                  className={cn(
                    'group relative flex h-28 flex-col gap-1 border-r border-border p-1.5 text-left transition-colors last:border-r-0',
                    'hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/60',
                    !inMonth && 'bg-muted/10',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className={cn(
                        'inline-flex h-6 w-6 items-center justify-center rounded-full text-xs tabular-nums',
                        isToday
                          ? 'bg-brand font-semibold text-brand-foreground'
                          : inMonth
                          ? 'text-foreground'
                          : 'text-muted-foreground/50',
                      )}
                    >
                      {d.getDate()}
                    </span>
                    {dayItems.length > 0 && !isToday && (
                      <span className="rounded-full bg-muted px-1.5 py-0 text-[10px] font-medium tabular-nums text-muted-foreground">
                        {dayItems.length}
                      </span>
                    )}
                  </div>

                  <div className="flex flex-1 flex-col gap-0.5 overflow-hidden">
                    {preview.map((item) => (
                      <ItemDot
                        key={`${item.kind}-${item.id}`}
                        item={item}
                        onClick={(it, e) => {
                          e.stopPropagation()
                          onItemClick?.(it)
                        }}
                      />
                    ))}
                    {extra > 0 && (
                      <span className="px-1.5 text-[10px] text-muted-foreground">
                        +{extra} mehr
                      </span>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        ))}
      </div>

      <DaySheet
        open={selectedDate !== null}
        date={selectedDate}
        items={selectedItems}
        now={now}
        onClose={closeDay}
        onItemClick={(item) => {
          closeDay()
          onItemClick?.(item)
        }}
      />
    </>
  )
}
