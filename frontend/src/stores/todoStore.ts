import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '@/lib/api'
import { toast } from '@/stores/toastStore'
import type { Todo, TodoStatus, Priority } from '@/lib/types'

interface TodoCreate {
  title: string
  description?: string
  project_id?: string | null
  status?: TodoStatus
  priority?: Priority
  deadline?: string | null
  tags?: string[]
}

export type KanbanDensity = 'compact' | 'comfortable' | 'spacious'
export type KanbanGroupBy = 'none' | 'assignee' | 'priority' | 'label'

interface TodoStore {
  todos: Todo[]
  loading: boolean
  error: string | null

  filterProjectId: string | null
  filterStatus: string | null
  filterPriority: string | null

  kanbanDensity: KanbanDensity
  kanbanGroupBy: KanbanGroupBy
  kanbanWipLimits: Partial<Record<TodoStatus, number>>

  setFilter: (key: 'filterProjectId' | 'filterStatus' | 'filterPriority', value: string | null) => void
  setDensity: (density: KanbanDensity) => void
  cycleDensity: (dir: 1 | -1) => void
  setGroupBy: (group: KanbanGroupBy) => void
  setWipLimit: (status: TodoStatus, limit: number | null) => void

  fetchTodos: (projectId?: string | null) => Promise<void>
  createTodo: (data: TodoCreate) => Promise<Todo>
  updateTodo: (id: string, data: Partial<TodoCreate>) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  updateStatus: (id: string, status: TodoStatus, kanbanOrder?: number) => Promise<void>
  updateOrder: (id: string, kanbanOrder: number) => Promise<void>
  bulkUpdateStatus: (ids: string[], status: TodoStatus) => Promise<void>
  bulkDelete: (ids: string[]) => Promise<void>
  getTodosByStatus: (status: TodoStatus) => Todo[]
}

const DENSITY_ORDER: KanbanDensity[] = ['compact', 'comfortable', 'spacious']

export const useTodoStore = create<TodoStore>()(
  persist(
    (set, get) => ({
  todos: [],
  loading: false,
  error: null,
  filterProjectId: null,
  filterStatus: null,
  filterPriority: null,

  kanbanDensity: 'comfortable',
  kanbanGroupBy: 'none',
  kanbanWipLimits: {},

  setFilter: (key, value) => set({ [key]: value }),

  setDensity: (density) => set({ kanbanDensity: density }),
  cycleDensity: (dir) => {
    const idx = DENSITY_ORDER.indexOf(get().kanbanDensity)
    const next = DENSITY_ORDER[(idx + dir + DENSITY_ORDER.length) % DENSITY_ORDER.length]
    set({ kanbanDensity: next })
  },
  setGroupBy: (group) => set({ kanbanGroupBy: group }),
  setWipLimit: (status, limit) =>
    set((state) => {
      const next = { ...state.kanbanWipLimits }
      if (limit === null || limit <= 0) delete next[status]
      else next[status] = limit
      return { kanbanWipLimits: next }
    }),

  fetchTodos: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const params = new URLSearchParams()
      const pid = projectId !== undefined ? projectId : get().filterProjectId
      if (pid) params.set('project_id', pid)
      if (get().filterStatus) params.set('status', get().filterStatus!)
      if (get().filterPriority) params.set('priority', get().filterPriority!)
      const qs = params.toString() ? `?${params}` : ''
      const todos = await api.get<Todo[]>(`/todos${qs}`)
      set({ todos, loading: false })
    } catch (e) {
      const msg = (e as Error).message
      set({ error: msg, loading: false })
      toast.error('Todos konnten nicht geladen werden', { description: msg })
    }
  },

  createTodo: async (data) => {
    const todo = await api.post<Todo>('/todos', data)
    // Optimistic: append to local state
    set((state) => ({ todos: [...state.todos, todo] }))
    return todo
  },

  updateTodo: async (id, data) => {
    // Optimistic: update local state immediately
    set((state) => ({
      todos: state.todos.map((t) => (t.id === id ? { ...t, ...data } : t)),
    }))
    await api.put(`/todos/${id}`, data)
  },

  deleteTodo: async (id) => {
    // Optimistic: remove from local state immediately
    set((state) => ({ todos: state.todos.filter((t) => t.id !== id) }))
    await api.del(`/todos/${id}`)
  },

  updateStatus: async (id, status, kanbanOrder) => {
    // Optimistic: update local state immediately
    set((state) => ({
      todos: state.todos.map((t) =>
        t.id === id ? { ...t, status, kanban_order: kanbanOrder ?? t.kanban_order } : t
      ),
    }))
    await api.patch(`/todos/${id}/status`, { status, kanban_order: kanbanOrder })
  },

  updateOrder: async (id, kanbanOrder) => {
    set((state) => ({
      todos: state.todos.map((t) => (t.id === id ? { ...t, kanban_order: kanbanOrder } : t)),
    }))
    await api.patch(`/todos/${id}/order`, { kanban_order: kanbanOrder })
  },

  bulkUpdateStatus: async (ids, status) => {
    if (ids.length === 0) return
    set((state) => ({
      todos: state.todos.map((t) => (ids.includes(t.id) ? { ...t, status } : t)),
    }))
    await Promise.all(ids.map((id) => api.patch(`/todos/${id}/status`, { status })))
  },

  bulkDelete: async (ids) => {
    if (ids.length === 0) return
    set((state) => ({ todos: state.todos.filter((t) => !ids.includes(t.id)) }))
    await Promise.all(ids.map((id) => api.del(`/todos/${id}`)))
  },

  getTodosByStatus: (status) => {
    return get().todos.filter((t) => t.status === status).sort((a, b) => a.kanban_order - b.kanban_order)
  },
    }),
    {
      name: 'projecthub.kanban-prefs',
      partialize: (state) => ({
        kanbanDensity: state.kanbanDensity,
        kanbanGroupBy: state.kanbanGroupBy,
        kanbanWipLimits: state.kanbanWipLimits,
        filterProjectId: state.filterProjectId,
        filterStatus: state.filterStatus,
        filterPriority: state.filterPriority,
      }),
    },
  ),
)
