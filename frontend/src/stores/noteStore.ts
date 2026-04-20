import { create } from 'zustand'
import { api } from '@/lib/api'
import type { Note } from '@/lib/types'

interface NoteCreate {
  project_id: string
  title?: string
  content?: string
  content_format?: string
  deadline?: string | null
  tags?: string[]
}

interface NoteStore {
  notes: Note[]
  loading: boolean
  error: string | null

  fetchNotes: (projectId?: string) => Promise<void>
  createNote: (data: NoteCreate) => Promise<Note>
  updateNote: (id: string, data: Partial<NoteCreate>) => Promise<void>
  deleteNote: (id: string) => Promise<void>
  togglePin: (id: string) => Promise<void>
  addLinkedKnowledge: (noteId: string, knowledgeId: string) => Promise<void>
  removeLinkedKnowledge: (noteId: string, knowledgeId: string) => Promise<void>
}

export const useNoteStore = create<NoteStore>((set, get) => ({
  notes: [],
  loading: false,
  error: null,

  fetchNotes: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const qs = projectId ? `?project_id=${projectId}` : ''
      const notes = await api.get<Note[]>(`/notes${qs}`)
      set({ notes, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  createNote: async (data) => {
    const note = await api.post<Note>('/notes', data)
    // Optimistic: append
    set((state) => ({ notes: [...state.notes, note] }))
    return note
  },

  updateNote: async (id, data) => {
    // Optimistic: update local
    set((state) => ({
      notes: state.notes.map((n) => (n.id === id ? { ...n, ...data } : n)),
    }))
    await api.put(`/notes/${id}`, data)
  },

  deleteNote: async (id) => {
    // Optimistic: remove
    set((state) => ({ notes: state.notes.filter((n) => n.id !== id) }))
    await api.del(`/notes/${id}`)
  },

  togglePin: async (id) => {
    // Optimistic: toggle local
    set((state) => ({
      notes: state.notes.map((n) =>
        n.id === id ? { ...n, is_pinned: !n.is_pinned } : n
      ),
    }))
    await api.patch(`/notes/${id}/pin`, {})
  },

  addLinkedKnowledge: async (noteId, knowledgeId) => {
    set((state) => ({
      notes: state.notes.map((n) => {
        if (n.id === noteId) {
          const linked = new Set(n.linked_knowledge_ids || [])
          linked.add(knowledgeId)
          return { ...n, linked_knowledge_ids: Array.from(linked) }
        }
        return n
      }),
    }))
    await api.patch(`/notes/${noteId}/linked-knowledge`, { knowledge_id: knowledgeId })
  },

  removeLinkedKnowledge: async (noteId, knowledgeId) => {
    set((state) => ({
      notes: state.notes.map((n) => {
        if (n.id === noteId) {
          const linked = new Set(n.linked_knowledge_ids || [])
          linked.delete(knowledgeId)
          return { ...n, linked_knowledge_ids: Array.from(linked) }
        }
        return n
      }),
    }))
    await api.patch(`/notes/${noteId}/linked-knowledge`, { knowledge_id: knowledgeId, remove: true })
  },
}))
