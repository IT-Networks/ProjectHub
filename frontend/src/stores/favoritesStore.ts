import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '@/lib/api'

export interface Favorite {
  id: string
  type: 'project' | 'todo' | 'note'
  title: string
  icon?: string
  addedAt: Date
  order: number
}

interface FavoritesState {
  favorites: Favorite[]
  recentItems: Array<{ id: string; type: 'project' | 'todo' | 'note'; title: string; accessedAt: Date }>

  // Favorites management
  addFavorite: (id: string, type: 'project' | 'todo' | 'note', title: string, icon?: string) => Promise<void>
  removeFavorite: (id: string) => Promise<void>
  isFavorited: (id: string) => boolean
  getFavorites: () => Favorite[]
  reorderFavorites: (newOrder: Favorite[]) => Promise<void>

  // Recent items
  addRecentItem: (id: string, type: 'project' | 'todo' | 'note', title: string) => void
  getRecentItems: () => Array<{ id: string; type: 'project' | 'todo' | 'note'; title: string; accessedAt: Date }>
}

export const useFavoritesStore = create<FavoritesState>()(
  persist(
    (set, get) => ({
      favorites: [],
      recentItems: [],

      addFavorite: async (id: string, type: 'project' | 'todo' | 'note', title: string, icon?: string) => {
        const state = get()
        const exists = state.favorites.some((f) => f.id === id)

        if (!exists) {
          const newFavorite: Favorite = {
            id,
            type,
            title,
            icon,
            addedAt: new Date(),
            order: state.favorites.length,
          }

          set((state) => ({
            favorites: [...state.favorites, newFavorite],
          }))

          // Sync to backend
          try {
            await api.post('/favorites', { id, type, title })
          } catch (err) {
            console.error('Failed to sync favorite to backend:', err)
          }
        }
      },

      removeFavorite: async (id: string) => {
        set((state) => ({
          favorites: state.favorites.filter((f) => f.id !== id),
        }))

        // Sync to backend
        try {
          await api.delete(`/favorites/${id}`)
        } catch (err) {
          console.error('Failed to remove favorite from backend:', err)
        }
      },

      isFavorited: (id: string) => {
        return get().favorites.some((f) => f.id === id)
      },

      getFavorites: () => {
        return get().favorites.sort((a, b) => a.order - b.order)
      },

      reorderFavorites: async (newOrder: Favorite[]) => {
        set(() => ({
          favorites: newOrder.map((f, idx) => ({ ...f, order: idx })),
        }))

        try {
          await api.post('/favorites/reorder', {
            order: newOrder.map((f) => f.id),
          })
        } catch (err) {
          console.error('Failed to reorder favorites:', err)
        }
      },

      addRecentItem: (id: string, type: 'project' | 'todo' | 'note', title: string) => {
        set((state) => {
          const filtered = state.recentItems.filter((item) => item.id !== id)
          return {
            recentItems: [
              { id, type, title, accessedAt: new Date() },
              ...filtered,
            ].slice(0, 10),
          }
        })
      },

      getRecentItems: () => {
        return get().recentItems
      },
    }),
    {
      name: 'favorites-store',
      partialize: (state) => ({
        favorites: state.favorites,
        recentItems: state.recentItems,
      }),
      merge: (persisted: any, current: any) => {
        if (!persisted) return current
        return {
          ...current,
          favorites: persisted.favorites || [],
          recentItems: (persisted.recentItems || []).map((item: any) => ({
            ...item,
            accessedAt: typeof item.accessedAt === 'string' ? new Date(item.accessedAt) : item.accessedAt,
          })),
        }
      },
    }
  )
)
