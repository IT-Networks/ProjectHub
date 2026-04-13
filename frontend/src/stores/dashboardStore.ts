import { create } from 'zustand'
import { api } from '@/lib/api'
import type { WidgetConfig } from '@/lib/types'

interface WidgetCreate {
  widget_type: string
  grid_col?: number
  grid_row?: number
  grid_width?: number
  grid_height?: number
  config?: Record<string, unknown>
}

interface DashboardStore {
  widgets: WidgetConfig[]
  loading: boolean

  fetchDashboard: (id?: string) => Promise<void>
  addWidget: (data: WidgetCreate, dashboardId?: string) => Promise<void>
  updateWidget: (widgetId: string, data: Partial<WidgetCreate & { is_visible?: boolean }>) => Promise<void>
  removeWidget: (widgetId: string) => Promise<void>
  updateLayout: (widgets: { id: string; grid_col: number; grid_row: number; grid_width: number; grid_height: number }[], dashboardId?: string) => Promise<void>
}

export const useDashboardStore = create<DashboardStore>((set, get) => ({
  widgets: [],
  loading: false,

  fetchDashboard: async (id = 'main') => {
    set({ loading: true })
    try {
      const data = await api.get<{ dashboard_id: string; widgets: WidgetConfig[] }>(`/dashboard/${id}`)
      set({ widgets: data.widgets, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  addWidget: async (data, dashboardId = 'main') => {
    await api.post(`/dashboard/${dashboardId}/widgets`, data)
    await get().fetchDashboard(dashboardId)
  },

  updateWidget: async (widgetId, data) => {
    await api.put(`/dashboard/widgets/${widgetId}`, data)
    await get().fetchDashboard()
  },

  removeWidget: async (widgetId) => {
    await api.del(`/dashboard/widgets/${widgetId}`)
    await get().fetchDashboard()
  },

  updateLayout: async (widgets, dashboardId = 'main') => {
    await api.put(`/dashboard/${dashboardId}`, { widgets })
    await get().fetchDashboard(dashboardId)
  },
}))
