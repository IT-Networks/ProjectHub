import { create } from 'zustand'

interface BulkSelectionState {
  selectedIds: Set<string>
  isSelectMode: boolean

  // Selection management
  selectItem: (id: string) => void
  deselectItem: (id: string) => void
  toggleItem: (id: string) => void
  selectAll: (ids: string[]) => void
  deselectAll: () => void
  isSelected: (id: string) => boolean
  getSelectedIds: () => string[]
  getSelectedCount: () => number

  // Mode management
  enterSelectMode: () => void
  exitSelectMode: () => void
  toggleSelectMode: () => void
}

export const useBulkSelectionStore = create<BulkSelectionState>((set, get) => ({
  selectedIds: new Set(),
  isSelectMode: false,

  selectItem: (id: string) => {
    set((state) => {
      const newIds = new Set(state.selectedIds)
      newIds.add(id)
      return { selectedIds: newIds, isSelectMode: true }
    })
  },

  deselectItem: (id: string) => {
    set((state) => {
      const newIds = new Set(state.selectedIds)
      newIds.delete(id)
      return {
        selectedIds: newIds,
        isSelectMode: newIds.size > 0 ? true : false,
      }
    })
  },

  toggleItem: (id: string) => {
    const state = get()
    if (state.isSelected(id)) {
      state.deselectItem(id)
    } else {
      state.selectItem(id)
    }
  },

  selectAll: (ids: string[]) => {
    set({
      selectedIds: new Set(ids),
      isSelectMode: true,
    })
  },

  deselectAll: () => {
    set({
      selectedIds: new Set(),
      isSelectMode: false,
    })
  },

  isSelected: (id: string) => {
    return get().selectedIds.has(id)
  },

  getSelectedIds: () => {
    return Array.from(get().selectedIds)
  },

  getSelectedCount: () => {
    return get().selectedIds.size
  },

  enterSelectMode: () => {
    set({ isSelectMode: true })
  },

  exitSelectMode: () => {
    set({ isSelectMode: false, selectedIds: new Set() })
  },

  toggleSelectMode: () => {
    set((state) => ({
      isSelectMode: !state.isSelectMode,
      selectedIds: !state.isSelectMode ? state.selectedIds : new Set(),
    }))
  },
}))
