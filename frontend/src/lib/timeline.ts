import type {
  Note,
  Priority,
  ProjectListItem,
  Todo,
  TodoStatus,
  User,
} from './types'

// ═════════════════════════════════════════════════════════════════════
// Types
// ═════════════════════════════════════════════════════════════════════

export type TimelineItemKind = 'todo' | 'note'

export interface TimelineItem {
  id: string
  kind: TimelineItemKind
  title: string
  description: string
  at: string
  completed: boolean
  project_id: string | null
  project_name: string | null
  project_color: string | null
  priority?: Priority
  status?: TodoStatus
  tags: string[]
  assignee_id?: string | null
  assignee?: User | null
  raw: Todo | Note
}

export type BucketId =
  | 'overdue'
  | 'today'
  | 'tomorrow'
  | 'this_week'
  | 'next_week'
  | 'this_month'
  | 'later'
  | 'undated'
  | 'completed'

export type BucketTone = 'danger' | 'brand' | 'neutral' | 'muted' | 'success'

export interface Bucket {
  id: BucketId
  label: string
  tone: BucketTone
  items: TimelineItem[]
}

export interface TimelineFilter {
  projectId: string | null
  priority: Priority | null
  kind: TimelineItemKind | null
  tag: string | null
  showCompleted: boolean
  q: string
}

export const EMPTY_FILTER: TimelineFilter = {
  projectId: null,
  priority: null,
  kind: null,
  tag: null,
  showCompleted: false,
  q: '',
}

export type GanttLane = 'project' | 'priority' | 'assignee'

// ═════════════════════════════════════════════════════════════════════
// Date utilities (pure, timezone-safe w.r.t. local wall-clock)
// ═════════════════════════════════════════════════════════════════════

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

export function addDays(d: Date, n: number): Date {
  const r = new Date(d)
  r.setDate(r.getDate() + n)
  return r
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

export function isSameMonth(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
}

export function startOfISOWeek(d: Date): Date {
  const day = d.getDay() // 0=Sun, 1=Mon, ..., 6=Sat
  const deltaToMonday = day === 0 ? -6 : 1 - day
  return startOfDay(addDays(d, deltaToMonday))
}

export function endOfISOWeek(d: Date): Date {
  return addDays(startOfISOWeek(d), 6)
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

export function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}

// ═════════════════════════════════════════════════════════════════════
// Transformation
// ═════════════════════════════════════════════════════════════════════

interface ToItemsInput {
  todos: readonly Todo[]
  notes: readonly Note[]
  projects: readonly ProjectListItem[]
}

export function toTimelineItems(input: ToItemsInput): TimelineItem[] {
  const projectMap = new Map<string, ProjectListItem>()
  for (const p of input.projects) projectMap.set(p.id, p)

  const items: TimelineItem[] = []

  for (const t of input.todos) {
    if (!t.deadline) continue
    const project = t.project_id ? projectMap.get(t.project_id) ?? null : null
    items.push({
      id: t.id,
      kind: 'todo',
      title: t.title,
      description: t.description,
      at: t.deadline,
      completed: t.status === 'done',
      project_id: t.project_id,
      project_name: project?.name ?? null,
      project_color: project?.color ?? null,
      priority: t.priority,
      status: t.status,
      tags: t.tags,
      assignee_id: t.assignee_id ?? null,
      assignee: t.assignee ?? null,
      raw: t,
    })
  }

  for (const n of input.notes) {
    if (!n.deadline) continue
    const project = projectMap.get(n.project_id) ?? null
    items.push({
      id: n.id,
      kind: 'note',
      title: n.title || 'Notiz',
      description: '',
      at: n.deadline,
      completed: false,
      project_id: n.project_id,
      project_name: project?.name ?? null,
      project_color: project?.color ?? null,
      tags: n.tags,
      raw: n,
    })
  }

  items.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())
  return items
}

// ═════════════════════════════════════════════════════════════════════
// Filtering
// ═════════════════════════════════════════════════════════════════════

export function filterItems(
  items: readonly TimelineItem[],
  filter: TimelineFilter,
): TimelineItem[] {
  const q = filter.q.trim().toLowerCase()
  return items.filter((it) => {
    if (filter.projectId && it.project_id !== filter.projectId) return false
    if (filter.priority && it.priority !== filter.priority) return false
    if (filter.kind && it.kind !== filter.kind) return false
    if (filter.tag && !it.tags.includes(filter.tag)) return false
    if (!filter.showCompleted && it.completed) return false
    if (q) {
      const hay =
        it.title.toLowerCase() +
        ' ' +
        it.description.toLowerCase() +
        ' ' +
        it.tags.join(' ').toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
}

// ═════════════════════════════════════════════════════════════════════
// Schedule: Bucket-Grouping
// ═════════════════════════════════════════════════════════════════════

const BUCKET_LABELS: Record<BucketId, string> = {
  overdue: 'Überfällig',
  today: 'Heute',
  tomorrow: 'Morgen',
  this_week: 'Diese Woche',
  next_week: 'Nächste Woche',
  this_month: 'Dieser Monat',
  later: 'Später',
  undated: 'Ohne Datum',
  completed: 'Erledigt',
}

const BUCKET_TONES: Record<BucketId, BucketTone> = {
  overdue: 'danger',
  today: 'brand',
  tomorrow: 'neutral',
  this_week: 'neutral',
  next_week: 'neutral',
  this_month: 'neutral',
  later: 'muted',
  undated: 'muted',
  completed: 'success',
}

const BUCKET_ORDER: BucketId[] = [
  'overdue',
  'today',
  'tomorrow',
  'this_week',
  'next_week',
  'this_month',
  'later',
  'undated',
  'completed',
]

function classifyItem(
  item: TimelineItem,
  ctx: {
    today: Date
    tomorrow: Date
    weekEnd: Date
    nextWeekEnd: Date
    monthEnd: Date
  },
): BucketId {
  if (item.completed) return 'completed'

  const at = new Date(item.at)
  if (isNaN(at.getTime())) return 'undated'
  const atDay = startOfDay(at)

  if (atDay < ctx.today) return 'overdue'
  if (isSameDay(atDay, ctx.today)) return 'today'
  if (isSameDay(atDay, ctx.tomorrow)) return 'tomorrow'
  if (atDay <= ctx.weekEnd) return 'this_week'
  if (atDay <= ctx.nextWeekEnd) return 'next_week'
  if (atDay <= ctx.monthEnd) return 'this_month'
  return 'later'
}

export function groupByBucket(
  items: readonly TimelineItem[],
  opts: { now: Date; showCompleted: boolean },
): Bucket[] {
  const today = startOfDay(opts.now)
  const tomorrow = addDays(today, 1)
  const weekEnd = endOfISOWeek(today)
  const nextWeekEnd = addDays(weekEnd, 7)
  const monthEnd = endOfMonth(today)

  const byId = new Map<BucketId, TimelineItem[]>()

  for (const item of items) {
    const id = classifyItem(item, { today, tomorrow, weekEnd, nextWeekEnd, monthEnd })
    if (id === 'completed' && !opts.showCompleted) continue
    const arr = byId.get(id)
    if (arr) arr.push(item)
    else byId.set(id, [item])
  }

  const result: Bucket[] = []
  for (const id of BUCKET_ORDER) {
    const arr = byId.get(id)
    if (!arr || arr.length === 0) continue
    result.push({
      id,
      label: BUCKET_LABELS[id],
      tone: BUCKET_TONES[id],
      items: arr,
    })
  }
  return result
}

// ═════════════════════════════════════════════════════════════════════
// Calendar: Day-Grouping (used in T2)
// ═════════════════════════════════════════════════════════════════════

function toDayKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${dd}`
}

export function daysInMonthGrid(cursor: Date): Date[] {
  const start = startOfISOWeek(startOfMonth(cursor))
  return Array.from({ length: 42 }, (_, i) => addDays(start, i))
}

export function groupByDay(
  items: readonly TimelineItem[],
  range: { from: Date; to: Date },
): Map<string, TimelineItem[]> {
  const out = new Map<string, TimelineItem[]>()
  const from = startOfDay(range.from).getTime()
  const to = startOfDay(range.to).getTime()
  for (const it of items) {
    const at = new Date(it.at)
    if (isNaN(at.getTime())) continue
    const key = toDayKey(startOfDay(at))
    const dayTs = startOfDay(at).getTime()
    if (dayTs < from || dayTs > to) continue
    const arr = out.get(key)
    if (arr) arr.push(it)
    else out.set(key, [it])
  }
  return out
}

// ═════════════════════════════════════════════════════════════════════
// Gantt: Window, Ticks, Pixel-Math
// ═════════════════════════════════════════════════════════════════════

export type GanttZoom = 'day' | 'week' | 'month'

export const PX_PER_DAY: Record<GanttZoom, number> = {
  day: 64,
  week: 18,
  month: 6,
}

export interface GanttWindow {
  from: Date
  to: Date
  totalDays: number
}

export function ganttWindow(cursor: Date, zoom: GanttZoom): GanttWindow {
  const base = startOfDay(cursor)
  let from: Date
  let totalDays: number

  if (zoom === 'day') {
    from = addDays(base, -3)
    totalDays = 30
  } else if (zoom === 'week') {
    from = addDays(startOfISOWeek(base), -7)
    totalDays = 12 * 7
  } else {
    from = startOfMonth(addDays(startOfMonth(base), -1))
    const monthsAhead = 6
    const afterEnd = new Date(from.getFullYear(), from.getMonth() + monthsAhead, 1)
    totalDays = Math.round((afterEnd.getTime() - from.getTime()) / 86_400_000)
  }

  const to = addDays(from, totalDays - 1)
  return { from, to, totalDays }
}

export function xForDate(date: Date, from: Date, pxPerDay: number): number {
  const diff = (startOfDay(date).getTime() - startOfDay(from).getTime()) / 86_400_000
  return diff * pxPerDay
}

export interface GanttTick {
  date: Date
  label: string
  level: 'minor' | 'major'
  isWeekend?: boolean
  isToday?: boolean
}

export function ganttTicks(
  window: GanttWindow,
  zoom: GanttZoom,
  now: Date,
): { major: GanttTick[]; minor: GanttTick[] } {
  const today = startOfDay(now)
  const major: GanttTick[] = []
  const minor: GanttTick[] = []

  if (zoom === 'day') {
    let cursor = new Date(window.from)
    let currentMonth = -1
    while (cursor <= window.to) {
      if (cursor.getMonth() !== currentMonth) {
        currentMonth = cursor.getMonth()
        major.push({
          date: new Date(cursor),
          label: cursor.toLocaleDateString('de-DE', { month: 'long', year: 'numeric' }),
          level: 'major',
        })
      }
      const dow = cursor.getDay()
      minor.push({
        date: new Date(cursor),
        label: String(cursor.getDate()),
        level: 'minor',
        isWeekend: dow === 0 || dow === 6,
        isToday: isSameDay(cursor, today),
      })
      cursor = addDays(cursor, 1)
    }
    return { major, minor }
  }

  if (zoom === 'week') {
    let cursor = startOfISOWeek(window.from)
    let currentMonth = -1
    while (cursor <= window.to) {
      if (cursor.getMonth() !== currentMonth) {
        currentMonth = cursor.getMonth()
        major.push({
          date: new Date(cursor),
          label: cursor.toLocaleDateString('de-DE', { month: 'short', year: '2-digit' }),
          level: 'major',
        })
      }
      minor.push({
        date: new Date(cursor),
        label: `${cursor.getDate()}.`,
        level: 'minor',
        isToday: cursor <= today && addDays(cursor, 6) >= today,
      })
      cursor = addDays(cursor, 7)
    }
    return { major, minor }
  }

  // month
  let cursor = startOfMonth(window.from)
  let currentYear = -1
  while (cursor <= window.to) {
    if (cursor.getFullYear() !== currentYear) {
      currentYear = cursor.getFullYear()
      major.push({
        date: new Date(cursor),
        label: String(cursor.getFullYear()),
        level: 'major',
      })
    }
    minor.push({
      date: new Date(cursor),
      label: cursor.toLocaleDateString('de-DE', { month: 'short' }),
      level: 'minor',
      isToday: isSameMonth(cursor, today),
    })
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1)
  }
  return { major, minor }
}

// ═════════════════════════════════════════════════════════════════════
// Gantt: Swimlanes
// ═════════════════════════════════════════════════════════════════════

export interface Swimlane {
  id: string
  label: string
  color?: string | null
  items: TimelineItem[]
}

const UNASSIGNED_LANE_ID = '__unassigned__'

export function groupByLane(
  items: readonly TimelineItem[],
  lane: GanttLane,
): Swimlane[] {
  const map = new Map<string, Swimlane>()

  const getOrCreate = (id: string, label: string, color?: string | null): Swimlane => {
    const existing = map.get(id)
    if (existing) return existing
    const created: Swimlane = { id, label, color: color ?? null, items: [] }
    map.set(id, created)
    return created
  }

  for (const item of items) {
    let laneId: string
    let laneLabel: string
    let laneColor: string | null = null

    if (lane === 'project') {
      if (item.project_id) {
        laneId = item.project_id
        laneLabel = item.project_name ?? 'Unbenanntes Projekt'
        laneColor = item.project_color ?? null
      } else {
        laneId = UNASSIGNED_LANE_ID
        laneLabel = 'Ohne Projekt'
      }
    } else if (lane === 'priority') {
      if (item.priority) {
        laneId = `prio-${item.priority}`
        laneLabel =
          item.priority === 'high' ? 'Hoch' : item.priority === 'medium' ? 'Mittel' : 'Niedrig'
      } else {
        laneId = 'prio-none'
        laneLabel = 'Ohne Priorität'
      }
    } else {
      if (item.assignee_id) {
        laneId = `user-${item.assignee_id}`
        laneLabel = item.assignee?.name ?? item.assignee_id
      } else {
        laneId = UNASSIGNED_LANE_ID
        laneLabel = 'Nicht zugewiesen'
      }
    }

    getOrCreate(laneId, laneLabel, laneColor).items.push(item)
  }

  const result = Array.from(map.values())
  if (lane === 'priority') {
    const order = ['prio-high', 'prio-medium', 'prio-low', 'prio-none']
    result.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id))
  } else {
    result.sort((a, b) => a.label.localeCompare(b.label, 'de'))
  }
  return result
}

// ═════════════════════════════════════════════════════════════════════
// Labels (i18n-ready; aktuell DE-hart)
// ═════════════════════════════════════════════════════════════════════

export function formatCursorLabel(cursor: Date, view: 'calendar' | 'gantt' | 'schedule'): string {
  if (view === 'gantt') {
    const end = endOfISOWeek(cursor)
    const sameYear = cursor.getFullYear() === end.getFullYear()
    const opts: Intl.DateTimeFormatOptions = { day: '2-digit', month: 'short' }
    const a = cursor.toLocaleDateString('de-DE', opts)
    const b = end.toLocaleDateString('de-DE', sameYear ? opts : { ...opts, year: 'numeric' })
    return `${a} – ${b}`
  }
  return cursor.toLocaleDateString('de-DE', { month: 'long', year: 'numeric' })
}
