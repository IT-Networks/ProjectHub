import { create } from 'zustand'
import { api } from '@/lib/api'
import type { QueueItem } from '@/lib/types'

interface QueueStats {
  pending: number
  accepted: number
  rejected: number
}

interface TodoQueueStore {
  items: QueueItem[]
  stats: QueueStats
  loading: boolean

  fetchQueue: (status?: string) => Promise<void>
  fetchStats: () => Promise<void>
  acceptItem: (id: string, projectId?: string | null) => Promise<void>
  rejectItem: (id: string) => Promise<void>
  updateItem: (id: string, data: Partial<QueueItem>) => Promise<void>
}

export const useTodoQueueStore = create<TodoQueueStore>((set, get) => ({
  items: [],
  stats: { pending: 0, accepted: 0, rejected: 0 },
  loading: false,

  fetchQueue: async (status) => {
    set({ loading: true })
    try {
      const qs = status ? `?queue_status=${status}` : ''
      const items = await api.get<QueueItem[]>(`/todo-queue${qs}`)
      set({ items, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  fetchStats: async () => {
    try {
      const stats = await api.get<QueueStats>('/todo-queue/stats')
      set({ stats })
    } catch { /* ignore */ }
  },

  acceptItem: async (id, projectId) => {
    await api.post(`/todo-queue/${id}/accept`, projectId ? { project_id: projectId } : {})
    await get().fetchQueue('pending')
    await get().fetchStats()
  },

  rejectItem: async (id) => {
    await api.post(`/todo-queue/${id}/reject`)
    await get().fetchQueue('pending')
    await get().fetchStats()
  },

  updateItem: async (id, data) => {
    await api.put(`/todo-queue/${id}`, data)
    await get().fetchQueue('pending')
  },
}))
