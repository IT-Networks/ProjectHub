import { create } from 'zustand'
import { api } from '@/lib/api'
import type {
  KnowledgeItem,
  KnowledgeItemCreate,
  KnowledgeItemUpdate,
  KnowledgeItemDetail,
  GraphData,
  KnowledgeStats,
  KnowledgeSearchResult,
  KnowledgeEdge,
  EdgeCreate,
  ProjectDocument,
  SuggestedEdge,
} from '@/lib/types'

type ViewMode = 'graph' | 'list' | 'split'

interface KnowledgeStore {
  items: KnowledgeItem[]
  graphData: GraphData | null
  stats: KnowledgeStats | null
  documents: ProjectDocument[]
  selectedItemId: string | null
  selectedItemDetail: KnowledgeItemDetail | null
  searchResults: KnowledgeSearchResult[]
  searchQuery: string
  filterCategory: string | null
  filterTag: string | null
  viewMode: ViewMode
  loading: boolean
  error: string | null

  fetchItems: (projectId: string) => Promise<void>
  fetchGraph: (projectId: string) => Promise<void>
  fetchStats: (projectId: string) => Promise<void>
  fetchDocuments: (projectId: string) => Promise<void>
  fetchItemDetail: (projectId: string, itemId: string) => Promise<void>
  createItem: (projectId: string, data: KnowledgeItemCreate) => Promise<KnowledgeItem>
  updateItem: (projectId: string, itemId: string, data: KnowledgeItemUpdate) => Promise<void>
  deleteItem: (projectId: string, itemId: string) => Promise<void>
  createEdge: (projectId: string, data: EdgeCreate) => Promise<KnowledgeEdge>
  deleteEdge: (projectId: string, edgeId: string) => Promise<void>
  searchItems: (projectId: string, query: string) => Promise<void>
  researchTopic: (projectId: string, topic: string) => Promise<KnowledgeItem>
  suggestLinks: (projectId: string, itemId: string) => Promise<SuggestedEdge[]>
  importNote: (projectId: string, noteId: string) => Promise<KnowledgeItem>
  importResearch: (projectId: string, researchId: string) => Promise<KnowledgeItem>

  setSelectedItem: (id: string | null) => void
  setViewMode: (mode: ViewMode) => void
  setFilterCategory: (cat: string | null) => void
  setFilterTag: (tag: string | null) => void
  setSearchQuery: (q: string) => void
  clearSearch: () => void
}

export const useKnowledgeStore = create<KnowledgeStore>((set, get) => ({
  items: [],
  graphData: null,
  stats: null,
  documents: [],
  selectedItemId: null,
  selectedItemDetail: null,
  searchResults: [],
  searchQuery: '',
  filterCategory: null,
  filterTag: null,
  viewMode: 'split',
  loading: false,
  error: null,

  fetchItems: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const params = new URLSearchParams()
      const { filterCategory, filterTag } = get()
      if (filterCategory) params.set('category', filterCategory)
      if (filterTag) params.set('tag', filterTag)
      const qs = params.toString() ? `?${params}` : ''
      const items = await api.get<KnowledgeItem[]>(`/knowledge/${projectId}${qs}`)
      set({ items, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  fetchGraph: async (projectId) => {
    try {
      const params = new URLSearchParams()
      const { filterCategory, filterTag } = get()
      if (filterCategory) params.set('category', filterCategory)
      if (filterTag) params.set('tag', filterTag)
      const qs = params.toString() ? `?${params}` : ''
      const graphData = await api.get<GraphData>(`/knowledge/${projectId}/graph${qs}`)
      set({ graphData })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  fetchStats: async (projectId) => {
    try {
      const stats = await api.get<KnowledgeStats>(`/knowledge/${projectId}/stats`)
      set({ stats })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  fetchDocuments: async (projectId) => {
    try {
      const documents = await api.get<ProjectDocument[]>(`/knowledge/${projectId}/documents`)
      set({ documents })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  fetchItemDetail: async (projectId, itemId) => {
    try {
      const detail = await api.get<KnowledgeItemDetail>(`/knowledge/${projectId}/${itemId}`)
      set({ selectedItemDetail: detail, selectedItemId: itemId })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  createItem: async (projectId, data) => {
    const item = await api.post<KnowledgeItem>(`/knowledge/${projectId}`, data)
    set((s) => ({ items: [item, ...s.items] }))
    return item
  },

  updateItem: async (projectId, itemId, data) => {
    set((s) => ({
      items: s.items.map((i) => (i.id === itemId ? { ...i, ...data } : i)),
    }))
    await api.put(`/knowledge/${projectId}/${itemId}`, data)
  },

  deleteItem: async (projectId, itemId) => {
    set((s) => ({
      items: s.items.filter((i) => i.id !== itemId),
      selectedItemId: s.selectedItemId === itemId ? null : s.selectedItemId,
      selectedItemDetail: s.selectedItemId === itemId ? null : s.selectedItemDetail,
    }))
    await api.del(`/knowledge/${projectId}/${itemId}`)
  },

  createEdge: async (projectId, data) => {
    const edge = await api.post<KnowledgeEdge>(`/knowledge/${projectId}/edges`, data)
    return edge
  },

  deleteEdge: async (projectId, edgeId) => {
    await api.del(`/knowledge/${projectId}/edges/${edgeId}`)
  },

  searchItems: async (projectId, query) => {
    if (!query.trim()) {
      set({ searchResults: [], searchQuery: '' })
      return
    }
    try {
      const results = await api.get<KnowledgeSearchResult[]>(
        `/knowledge/${projectId}/search?q=${encodeURIComponent(query)}`
      )
      set({ searchResults: results, searchQuery: query })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  researchTopic: async (projectId, topic) => {
    const item = await api.post<KnowledgeItem>(`/knowledge/${projectId}/research`, { topic })
    set((s) => ({ items: [item, ...s.items] }))
    return item
  },

  suggestLinks: async (projectId, itemId) => {
    const suggestions = await api.post<SuggestedEdge[]>(
      `/knowledge/${projectId}/suggest-links?item_id=${encodeURIComponent(itemId)}`
    )
    return suggestions
  },

  importNote: async (projectId, noteId) => {
    const item = await api.post<KnowledgeItem>(`/knowledge/${projectId}/import/note`, { note_id: noteId })
    set((s) => ({ items: [item, ...s.items] }))
    return item
  },

  importResearch: async (projectId, researchId) => {
    const item = await api.post<KnowledgeItem>(`/knowledge/${projectId}/import/research`, { research_id: researchId })
    set((s) => ({ items: [item, ...s.items] }))
    return item
  },

  setSelectedItem: (id) => set({ selectedItemId: id, selectedItemDetail: id ? get().selectedItemDetail : null }),
  setViewMode: (mode) => set({ viewMode: mode }),
  setFilterCategory: (cat) => set({ filterCategory: cat }),
  setFilterTag: (tag) => set({ filterTag: tag }),
  setSearchQuery: (q) => set({ searchQuery: q }),
  clearSearch: () => set({ searchResults: [], searchQuery: '' }),
}))
