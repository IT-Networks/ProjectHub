import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTimelineStore, type TimelineView, type TimelineZoom } from '@/stores/timelineStore'
import type { Priority } from '@/lib/types'
import type { GanttLane, TimelineItemKind } from '@/lib/timeline'

const VIEW_VALUES: TimelineView[] = ['schedule', 'calendar', 'gantt']
const ZOOM_VALUES: TimelineZoom[] = ['day', 'week', 'month']
const LANE_VALUES: GanttLane[] = ['project', 'priority', 'assignee']
const PRIORITY_VALUES: Priority[] = ['high', 'medium', 'low']
const KIND_VALUES: TimelineItemKind[] = ['todo', 'note']

function pick<T extends string>(v: string | null, allowed: T[]): T | null {
  return v && (allowed as string[]).includes(v) ? (v as T) : null
}

export function useTimelineUrlState() {
  const [searchParams, setSearchParams] = useSearchParams()
  const store = useTimelineStore()
  const hydratedRef = useRef(false)
  const qDebounceRef = useRef<number | null>(null)

  // URL → Store (once on mount, plus on back/forward)
  useEffect(() => {
    const view = pick(searchParams.get('view'), VIEW_VALUES)
    if (view) store.setView(view)

    const zoom = pick(searchParams.get('zoom'), ZOOM_VALUES)
    if (zoom) store.setZoom(zoom)

    const lane = pick(searchParams.get('lane'), LANE_VALUES)
    if (lane) store.setLane(lane)

    const cursor = searchParams.get('cursor')
    if (cursor) {
      const d = new Date(cursor)
      if (!isNaN(d.getTime())) store.setCursor(d.toISOString())
    }

    const patch: Partial<typeof store.filter> = {}
    const project = searchParams.get('project')
    if (project) patch.projectId = project
    const prio = pick(searchParams.get('prio'), PRIORITY_VALUES)
    if (prio) patch.priority = prio
    const kind = pick(searchParams.get('kind'), KIND_VALUES)
    if (kind) patch.kind = kind
    const tag = searchParams.get('tag')
    if (tag) patch.tag = tag
    const completed = searchParams.get('completed')
    if (completed === '1') patch.showCompleted = true
    else if (completed === '0') patch.showCompleted = false
    const q = searchParams.get('q')
    if (q) patch.q = q

    if (Object.keys(patch).length > 0) store.setFilter(patch)

    hydratedRef.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Store → URL (skip until hydrated, debounce q)
  useEffect(() => {
    if (!hydratedRef.current) return

    const next = new URLSearchParams()
    if (store.view !== 'schedule') next.set('view', store.view)
    if (store.view === 'gantt' && store.zoom !== 'week') next.set('zoom', store.zoom)
    if (store.view === 'gantt' && store.lane !== 'project') next.set('lane', store.lane)
    if (store.cursor) {
      const d = new Date(store.cursor)
      if (!isNaN(d.getTime())) {
        const y = d.getFullYear()
        const m = String(d.getMonth() + 1).padStart(2, '0')
        const dd = String(d.getDate()).padStart(2, '0')
        next.set('cursor', `${y}-${m}-${dd}`)
      }
    }
    if (store.filter.projectId) next.set('project', store.filter.projectId)
    if (store.filter.priority) next.set('prio', store.filter.priority)
    if (store.filter.kind) next.set('kind', store.filter.kind)
    if (store.filter.tag) next.set('tag', store.filter.tag)
    if (store.filter.showCompleted) next.set('completed', '1')

    const applyQ = () => {
      const withQ = new URLSearchParams(next)
      if (store.filter.q) withQ.set('q', store.filter.q)
      setSearchParams(withQ, { replace: true })
    }

    if (qDebounceRef.current !== null) window.clearTimeout(qDebounceRef.current)
    if (store.filter.q) {
      qDebounceRef.current = window.setTimeout(applyQ, 200)
    } else {
      applyQ()
    }

    return () => {
      if (qDebounceRef.current !== null) {
        window.clearTimeout(qDebounceRef.current)
        qDebounceRef.current = null
      }
    }
  }, [
    store.view,
    store.zoom,
    store.lane,
    store.cursor,
    store.filter.projectId,
    store.filter.priority,
    store.filter.kind,
    store.filter.tag,
    store.filter.showCompleted,
    store.filter.q,
    setSearchParams,
  ])
}
