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

export const useNoteStore = create<NoteStore>((set) => ({
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
    const updated = await api.put<Note>(`/notes/${id}`, data)
    set((state) => ({
      notes: state.notes.map((n) => (n.id === id ? updated : n)),
    }))

    if ((updated.linked_knowledge_ids?.length ?? 0) > 0) {
      const knowledgeStore = await import('@/stores/knowledgeStore')
      await knowledgeStore.useKnowledgeStore.getState().syncNoteToKnowledge(
        updated.project_id,
        updated.id,
        updated.content,
        updated.title,
      )
    }
  },

  deleteNote: async (id) => {
    await api.del(`/notes/${id}`)
    set((state) => ({ notes: state.notes.filter((n) => n.id !== id) }))
  },

  togglePin: async (id) => {
    const updated = await api.patch<Note>(`/notes/${id}/pin`, {})
    set((state) => ({
      notes: state.notes.map((n) =>
        n.id === id ? updated : n
      ),
    }))
  },

  addLinkedKnowledge: async (noteId, knowledgeId) => {
    const updated = await api.patch<Note>(`/notes/${noteId}/linked-knowledge`, { knowledge_id: knowledgeId })
    set((state) => ({
      notes: state.notes.map((n) => (n.id === noteId ? updated : n)),
    }))
  },

  removeLinkedKnowledge: async (noteId, knowledgeId) => {
    const updated = await api.patch<Note>(`/notes/${noteId}/linked-knowledge`, { knowledge_id: knowledgeId, remove: true })
    set((state) => ({
      notes: state.notes.map((n) => (n.id === noteId ? updated : n)),
    }))
  },
}))
