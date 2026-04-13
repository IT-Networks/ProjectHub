import { useEffect, useRef, useCallback } from 'react'
import { create } from 'zustand'

interface SSEEvent {
  type: string
  data: unknown
}

type SSEListener = (data: unknown) => void

interface SSEStore {
  connected: boolean
  listeners: Map<string, Set<SSEListener>>
  setConnected: (v: boolean) => void
  addListener: (eventType: string, fn: SSEListener) => void
  removeListener: (eventType: string, fn: SSEListener) => void
  dispatch: (event: SSEEvent) => void
}

export const useSSEStore = create<SSEStore>((set, get) => ({
  connected: false,
  listeners: new Map(),

  setConnected: (connected) => set({ connected }),

  addListener: (eventType, fn) => {
    const listeners = get().listeners
    if (!listeners.has(eventType)) {
      listeners.set(eventType, new Set())
    }
    listeners.get(eventType)!.add(fn)
    set({ listeners: new Map(listeners) })
  },

  removeListener: (eventType, fn) => {
    const listeners = get().listeners
    listeners.get(eventType)?.delete(fn)
    set({ listeners: new Map(listeners) })
  },

  dispatch: (event) => {
    const listeners = get().listeners
    const fns = listeners.get(event.type)
    if (fns) {
      for (const fn of fns) {
        try { fn(event.data) } catch { /* ignore */ }
      }
    }
  },
}))

/**
 * Hook that manages the global SSE connection.
 * Call once in AppLayout.
 */
export function useSSEConnection() {
  const sourceRef = useRef<EventSource | null>(null)
  const { setConnected, dispatch } = useSSEStore()

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>

    function connect() {
      if (sourceRef.current) {
        sourceRef.current.close()
      }

      const es = new EventSource('/api/events')
      sourceRef.current = es

      es.onopen = () => setConnected(true)
      es.onerror = () => {
        setConnected(false)
        es.close()
        // Reconnect after 5s
        reconnectTimer = setTimeout(connect, 5000)
      }

      // Listen to named events
      for (const eventType of ['build_update', 'pr_update', 'queue_item', 'todo_update', 'ai_assist_status', 'polling_status']) {
        es.addEventListener(eventType, (e: MessageEvent) => {
          try {
            const data = JSON.parse(e.data)
            dispatch({ type: eventType, data })
          } catch { /* ignore parse errors */ }
        })
      }

      // Keepalive
      es.addEventListener('keepalive', () => {
        setConnected(true)
      })
    }

    connect()

    return () => {
      clearTimeout(reconnectTimer)
      sourceRef.current?.close()
    }
  }, [setConnected, dispatch])
}

/**
 * Subscribe to a specific SSE event type.
 */
export function useSSEEvent(eventType: string, handler: SSEListener) {
  const { addListener, removeListener } = useSSEStore()
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  const stableHandler = useCallback((data: unknown) => {
    handlerRef.current(data)
  }, [])

  useEffect(() => {
    addListener(eventType, stableHandler)
    return () => removeListener(eventType, stableHandler)
  }, [eventType, stableHandler, addListener, removeListener])
}
