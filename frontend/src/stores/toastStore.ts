import { create } from 'zustand'

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface ToastEntry {
  id: string
  type: ToastType
  message: string
  description?: string
  action?: { label: string; onClick: () => void }
  duration?: number
}

type ToastOptions = Omit<ToastEntry, 'id' | 'type' | 'message'>

interface ToastStore {
  toasts: ToastEntry[]
  push: (type: ToastType, message: string, options?: ToastOptions) => string
  dismiss: (id: string) => void
  clear: () => void
}

function genId() {
  return Math.random().toString(36).slice(2, 11)
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (type, message, options) => {
    const id = genId()
    set((state) => ({
      toasts: [...state.toasts, { id, type, message, ...options }],
    }))
    return id
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}))

// Imperative helpers for non-component callers (stores, utilities)
export const toast = {
  success: (message: string, options?: ToastOptions) =>
    useToastStore.getState().push('success', message, options),
  error: (message: string, options?: ToastOptions) =>
    useToastStore.getState().push('error', message, options),
  info: (message: string, options?: ToastOptions) =>
    useToastStore.getState().push('info', message, options),
  warning: (message: string, options?: ToastOptions) =>
    useToastStore.getState().push('warning', message, options),
}
