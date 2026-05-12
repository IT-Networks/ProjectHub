import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { TimelineFilter, GanttLane } from '@/lib/timeline'
import { EMPTY_FILTER } from '@/lib/timeline'

export type TimelineView = 'schedule' | 'calendar' | 'gantt'
export type TimelineZoom = 'day' | 'week' | 'month'

interface TimelineStoreState {
  view: TimelineView
  cursor: string
  zoom: TimelineZoom
  lane: GanttLane
  filter: TimelineFilter

  setView: (v: TimelineView) => void
  setCursor: (iso: string) => void
  shiftCursor: (units: number) => void
  goToday: () => void
  setZoom: (z: TimelineZoom) => void
  setLane: (l: GanttLane) => void
  setFilter: (patch: Partial<TimelineFilter>) => void
  resetFilter: () => void
}

function todayIso(): string {
  const d = new Date()
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString()
}

export const useTimelineStore = create<TimelineStoreState>()(
  persist(
    (set, get) => ({
      view: 'schedule',
      cursor: todayIso(),
      zoom: 'week',
      lane: 'project',
      filter: EMPTY_FILTER,

      setView: (v) => set({ view: v }),
      setCursor: (iso) => set({ cursor: iso }),
      shiftCursor: (units) => {
        const d = new Date(get().cursor)
        if (isNaN(d.getTime())) return
        const view = get().view
        const zoom = get().zoom
        if (view === 'calendar') {
          d.setMonth(d.getMonth() + units)
        } else if (view === 'gantt') {
          const factor = zoom === 'day' ? 1 : zoom === 'week' ? 7 : 30
          d.setDate(d.getDate() + units * factor)
        } else {
          d.setDate(d.getDate() + units)
        }
        set({ cursor: d.toISOString() })
      },
      goToday: () => set({ cursor: todayIso() }),
      setZoom: (z) => set({ zoom: z }),
      setLane: (l) => set({ lane: l }),
      setFilter: (patch) => set((s) => ({ filter: { ...s.filter, ...patch } })),
      resetFilter: () => set({ filter: EMPTY_FILTER }),
    }),
    {
      name: 'projecthub.timeline-prefs',
      partialize: (state) => ({
        view: state.view,
        zoom: state.zoom,
        lane: state.lane,
        filter: { showCompleted: state.filter.showCompleted },
      }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<TimelineStoreState>
        return {
          ...current,
          view: p.view ?? current.view,
          zoom: p.zoom ?? current.zoom,
          lane: p.lane ?? current.lane,
          filter: {
            ...current.filter,
            showCompleted: p.filter?.showCompleted ?? current.filter.showCompleted,
          },
        }
      },
    },
  ),
)
