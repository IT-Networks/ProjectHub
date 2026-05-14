import { create } from 'zustand'
import { api } from '@/lib/api'

// Feldnamen spiegeln die (normalisierte) Backend-Antwort von
// `routers/inbox.py` — NICHT die rohe AI-Assist-Form. Der Proxy übersetzt
// `email_id`→`id` und `preview`→`body_preview`.
export interface EmailResult {
  id: string
  subject: string
  sender: string
  sender_name?: string
  date: string
  body_preview?: string
  has_attachments?: boolean
  folder?: string
}

export interface EmailAttachment {
  name: string
  size: number
  content_type: string
}

export interface EmailDetail {
  id: string
  subject: string
  sender: string
  sender_name?: string
  to: string[]
  cc: string[]
  date: string
  body_text: string
  body_html?: string
  attachments: EmailAttachment[]
  folder?: string
}

// AI-Assist liefert Webex-Räume/-Nachrichten in snake_case — diese Typen
// folgen exakt der API-Form, damit kein erneuter Feld-Mismatch entsteht.
export interface WebexRoom {
  id: string
  title: string
  type: string
  last_activity?: string
}

export interface WebexMessage {
  id: string
  text: string
  person_email: string
  person_display_name?: string
  created: string
}

interface InboxStore {
  activeTab: 'email' | 'webex'

  // Email
  emails: EmailResult[]
  emailLoading: boolean
  emailLoaded: boolean
  emailQuery: string
  emailDetail: EmailDetail | null
  emailDetailLoading: boolean

  // Webex
  webexRooms: WebexRoom[]
  webexRoomsLoaded: boolean
  webexLoading: boolean
  selectedRoom: string | null
  webexMessages: WebexMessage[]
  webexMessagesLoading: boolean
  webexMessageCache: Record<string, WebexMessage[]>

  setTab: (tab: 'email' | 'webex') => void

  searchEmails: (query?: string, sender?: string, folder?: string) => Promise<void>
  openEmail: (emailId: string, folder?: string) => Promise<void>
  closeEmail: () => void

  fetchWebexRooms: (force?: boolean) => Promise<void>
  fetchWebexMessages: (roomId: string, force?: boolean) => Promise<void>
}

export const useInboxStore = create<InboxStore>((set, get) => ({
  activeTab: 'email',

  emails: [],
  emailLoading: false,
  emailLoaded: false,
  emailQuery: '',
  emailDetail: null,
  emailDetailLoading: false,

  webexRooms: [],
  webexRoomsLoaded: false,
  webexLoading: false,
  selectedRoom: null,
  webexMessages: [],
  webexMessagesLoading: false,
  webexMessageCache: {},

  setTab: (tab) => set({ activeTab: tab }),

  searchEmails: async (query = '', sender = '', folder = 'inbox') => {
    set({ emailLoading: true, emailQuery: query })
    try {
      const params = new URLSearchParams({ query, sender, folder, limit: '30' })
      const data = await api.get<{ results: EmailResult[]; total: number }>(`/inbox/emails?${params}`)
      set({ emails: data.results || [], emailLoading: false, emailLoaded: true })
    } catch {
      set({ emails: [], emailLoading: false, emailLoaded: true })
    }
  },

  openEmail: async (emailId, folder = 'inbox') => {
    set({ emailDetailLoading: true, emailDetail: null })
    try {
      const data = await api.get<{ email: EmailDetail }>(
        `/inbox/emails/${encodeURIComponent(emailId)}?folder=${encodeURIComponent(folder)}`,
      )
      set({ emailDetail: data.email, emailDetailLoading: false })
    } catch {
      // Fällt auf null zurück — die UI zeigt dann den Listen-Snapshot.
      set({ emailDetailLoading: false })
    }
  },

  closeEmail: () => set({ emailDetail: null, emailDetailLoading: false }),

  fetchWebexRooms: async (force = false) => {
    if (!force && get().webexRoomsLoaded && get().webexRooms.length > 0) return
    set({ webexLoading: true })
    try {
      const data = await api.get<{ rooms: WebexRoom[] }>('/inbox/webex/rooms')
      set({ webexRooms: data.rooms || [], webexLoading: false, webexRoomsLoaded: true })
    } catch {
      set({ webexRooms: [], webexLoading: false, webexRoomsLoaded: true })
    }
  },

  fetchWebexMessages: async (roomId, force = false) => {
    const cached = get().webexMessageCache[roomId]
    if (!force && cached) {
      // Cache-Treffer: sofort anzeigen, kein Roundtrip.
      set({ selectedRoom: roomId, webexMessages: cached, webexMessagesLoading: false })
      return
    }
    set({ selectedRoom: roomId, webexMessagesLoading: true, webexMessages: cached || [] })
    try {
      const data = await api.get<{ messages: WebexMessage[] }>(`/inbox/webex/rooms/${roomId}/messages`)
      const messages = data.messages || []
      // Race-Schutz: nur übernehmen, wenn der Raum noch aktiv ist.
      if (get().selectedRoom !== roomId) {
        set((s) => ({ webexMessageCache: { ...s.webexMessageCache, [roomId]: messages } }))
        return
      }
      set((s) => ({
        webexMessages: messages,
        webexMessagesLoading: false,
        webexMessageCache: { ...s.webexMessageCache, [roomId]: messages },
      }))
    } catch {
      if (get().selectedRoom === roomId) {
        set({ webexMessages: [], webexMessagesLoading: false })
      }
    }
  },
}))
