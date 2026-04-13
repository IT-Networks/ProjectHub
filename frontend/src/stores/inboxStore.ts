import { create } from 'zustand'
import { api } from '@/lib/api'

interface EmailResult {
  id: string
  subject: string
  sender: string
  date: string
  body_preview?: string
  has_attachments?: boolean
}

interface WebexRoom {
  id: string
  title: string
  type: string
  lastActivity?: string
}

interface WebexMessage {
  id: string
  text: string
  personEmail: string
  personDisplayName?: string
  created: string
}

interface InboxStore {
  activeTab: 'email' | 'webex'
  // Email
  emails: EmailResult[]
  emailLoading: boolean
  selectedEmail: unknown | null
  // Webex
  webexRooms: WebexRoom[]
  webexMessages: WebexMessage[]
  selectedRoom: string | null
  webexLoading: boolean

  setTab: (tab: 'email' | 'webex') => void
  searchEmails: (query?: string, sender?: string, folder?: string) => Promise<void>
  readEmail: (emailId: string) => Promise<void>
  fetchWebexRooms: () => Promise<void>
  fetchWebexMessages: (roomId: string) => Promise<void>
}

export const useInboxStore = create<InboxStore>((set) => ({
  activeTab: 'email',
  emails: [],
  emailLoading: false,
  selectedEmail: null,
  webexRooms: [],
  webexMessages: [],
  selectedRoom: null,
  webexLoading: false,

  setTab: (tab) => set({ activeTab: tab }),

  searchEmails: async (query = '', sender = '', folder = 'inbox') => {
    set({ emailLoading: true })
    try {
      const params = new URLSearchParams({ query, sender, folder, limit: '30' })
      const data = await api.get<{ results: EmailResult[]; total: number }>(`/inbox/emails?${params}`)
      set({ emails: data.results || [], emailLoading: false })
    } catch {
      set({ emails: [], emailLoading: false })
    }
  },

  readEmail: async (emailId) => {
    try {
      const data = await api.get(`/inbox/emails/${emailId}`)
      set({ selectedEmail: data })
    } catch { /* offline */ }
  },

  fetchWebexRooms: async () => {
    set({ webexLoading: true })
    try {
      const data = await api.get<{ rooms: WebexRoom[] }>('/inbox/webex/rooms')
      set({ webexRooms: data.rooms || [], webexLoading: false })
    } catch {
      set({ webexRooms: [], webexLoading: false })
    }
  },

  fetchWebexMessages: async (roomId) => {
    set({ selectedRoom: roomId, webexLoading: true })
    try {
      const data = await api.get<{ messages: WebexMessage[] }>(`/inbox/webex/rooms/${roomId}/messages`)
      set({ webexMessages: data.messages || [], webexLoading: false })
    } catch {
      set({ webexMessages: [], webexLoading: false })
    }
  },
}))
