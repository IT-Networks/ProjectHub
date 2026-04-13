import { create } from 'zustand'
import { api } from '@/lib/api'
import type { Todo, TodoStatus } from '@/lib/types'

interface TodoCreate {
  title: string
  description?: string
  project_id?: string | null
  status?: string
  priority?: string
  deadline?: string | null
  tags?: string[]
}

interface TodoStore {
  todos: Todo[]
  loading: boolean
  error: string | null

  filterProjectId: string | null
  filterStatus: string | null
  filterPriority: string | null

  setFilter: (key: 'filterProjectId' | 'filterStatus' | 'filterPriority', value: string | null) => void
  fetchTodos: (projectId?: string | null) => Promise<void>
  createTodo: (data: TodoCreate) => Promise<Todo>
  updateTodo: (id: string, data: Partial<TodoCreate>) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  updateStatus: (id: string, status: TodoStatus, kanbanOrder?: number) => Promise<void>
  updateOrder: (id: string, kanbanOrder: number) => Promise<void>
  getTodosByStatus: (status: TodoStatus) => Todo[]
}

export const useTodoStore = create<TodoStore>((set, get) => ({
  todos: [],
  loading: false,
  error: null,
  filterProjectId: null,
  filterStatus: null,
  filterPriority: null,

  setFilter: (key, value) => set({ [key]: value }),

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
      set({ error: (e as Error).message, loading: false })
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

  getTodosByStatus: (status) => {
    return get().todos.filter((t) => t.status === status).sort((a, b) => a.kanban_order - b.kanban_order)
  },
}))
