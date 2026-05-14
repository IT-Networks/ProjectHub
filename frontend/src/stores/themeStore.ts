import { create } from 'zustand'

type Theme = 'dark' | 'light'

interface ThemeStore {
  theme: Theme
  toggleTheme: () => void
  setTheme: (theme: Theme) => void
}

const getInitialTheme = (): Theme => {
  if (typeof window === 'undefined') return 'dark'
  const stored = localStorage.getItem('projecthub-theme')
  if (stored === 'light' || stored === 'dark') return stored
  return 'dark'
}

/**
 * Apply the theme to <html> so it covers portaled content (dialogs,
 * popovers, tooltips) which render outside the React app subtree.
 */
const applyTheme = (theme: Theme) => {
  if (typeof document === 'undefined') return
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  theme: getInitialTheme(),

  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark'
    localStorage.setItem('projecthub-theme', next)
    applyTheme(next)
    set({ theme: next })
  },

  setTheme: (theme) => {
    localStorage.setItem('projecthub-theme', theme)
    applyTheme(theme)
    set({ theme })
  },
}))

// Apply once on module load so the initial paint is themed (no FOUC).
applyTheme(useThemeStore.getState().theme)
