import { create } from 'zustand'
import { api } from '@/lib/api'
import type {
  Note,
  NoteCreate,
  NoteUpdate,
} from '@/lib/types'

interface NotesStore {
  notes: Note[]
  currentProjectNotes: Note[]
  loading: boolean
  error: string | null

  fetchProjectNotes: (projectId: string) => Promise<void>
  createNote: (data: NoteCreate) => Promise<Note>
  updateNote: (id: string, data: NoteUpdate) => Promise<Note>
  deleteNote: (id: string) => Promise<void>

  // Pin/Unpin
  pinNote: (id: string) => Promise<void>
  unpinNote: (id: string) => Promise<void>

  // Sync with Knowledge
  syncWithKnowledge: (projectId: string, knowledgeId: string) => Promise<void>
}

export const useNotesStore = create<NotesStore>((set, get) => ({
  notes: [],
  currentProjectNotes: [],
  loading: false,
  error: null,

  fetchProjectNotes: async (projectId: string) => {
    set({ loading: true, error: null })
    try {
      const notes = await api.get(`/projects/${projectId}/notes`)
      set({ currentProjectNotes: notes, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  createNote: async (data: NoteCreate) => {
    const note = await api.post<Note>('/notes', data)
    const updated = await get().fetchProjectNotes(data.projectId)
    return note
  },

  updateNote: async (id: string, data: NoteUpdate) => {
    const updated = await api.put<Note>(`/notes/${id}`, data)
    const updatedList = await get().fetchProjectNotes(get().currentProject?.id || '')
    set({ currentProjectNotes: updatedList })
    return updated
  },

  deleteNote: async (id: string) => {
    await api.del(`/notes/${id}`)
    const updatedList = await get().fetchProjectNotes(get().currentProject?.id || '')
    set({ currentProjectNotes: updatedList })
  },

  pinNote: async (id: string) => {
    const { currentProjectNotes } = get()
    const note = currentProjectNotes.find(n => n.id === id)
    if (!note) return

    // Optimistic update
    set({ currentProjectNotes: [...currentProjectNotes.map(n =>
      n.id === id ? { ...n, is_pinned: !n.is_pinned } : n
    )] })

    await get().updateNote(id, { is_pinned: !note.is_pinned })
  },

  unpinNote: async (id: string) => {
    const { currentProjectNotes } = get()
    const note = currentProjectNotes.find(n => n.id === id)
    if (!note) return

    // Optimistic update
    set({ currentProjectNotes: [...currentProjectNotes.map(n =>
      n.id === id ? { ...n, is_pinned: false } : n
    )] })

    await get().updateNote(id, { is_pinned: false })
  },

  syncWithKnowledge: async (projectId: string, knowledgeId: string) => {
    const state = get()
    const note = state.currentProjectNotes.find(n => n.project_id === projectId)
    if (!note) return

    // Optimistic update
    set({ currentProjectNotes: [...state.currentProjectNotes.map(n =>
      n.id === note.id ? {
        ...n,
        linked_knowledge_ids: Array.from(new Set([...(n.linked_knowledge_ids || []), knowledgeId]))
      } : n
    )] })

    await get().updateNote(note.id, { linked_knowledge_ids: [...(note.linked_knowledge_ids || []), knowledgeId] })
  },
}))
