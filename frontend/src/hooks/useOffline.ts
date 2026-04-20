import { create } from 'zustand'
import { useEffect } from 'react'
import { useSSEEvent } from './useSSE'

interface OfflineStore {
  aiAssistConnected: boolean
  setAiAssistConnected: (v: boolean) => void
}

export const useOfflineStore = create<OfflineStore>((set) => ({
  aiAssistConnected: false,
  setAiAssistConnected: (aiAssistConnected) => set({ aiAssistConnected }),
}))

/**
 * Hook to track AI-Assist connectivity.
 * Call once in AppLayout to start monitoring.
 */
export function useOfflineMonitor() {
  const { setAiAssistConnected } = useOfflineStore()

  // Listen to SSE events from polling service
  useSSEEvent('ai_assist_status', (data) => {
    const d = data as { connected: boolean }
    setAiAssistConnected(d.connected)
  })

  // Initial check
  useEffect(() => {
    // Use absolute URL to backend on port 3001
    const apiUrl = typeof window !== 'undefined' && window.location.port === '3000'
      ? 'http://localhost:3001/api/settings/ai-assist-status'
      : '/api/settings/ai-assist-status'

    fetch(apiUrl)
      .then((r) => r.json())
      .then((d) => setAiAssistConnected(d.connected))
      .catch(() => setAiAssistConnected(false))
  }, [setAiAssistConnected])
}

export function useIsOffline() {
  return !useOfflineStore((s) => s.aiAssistConnected)
}
