import { describe, it, expect, beforeEach } from 'vitest'
import { useThemeStore } from './themeStore'

describe('themeStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useThemeStore.setState({ theme: 'dark' })
  })

  it('toggleTheme flips between dark and light', () => {
    useThemeStore.getState().toggleTheme()
    expect(useThemeStore.getState().theme).toBe('light')
    useThemeStore.getState().toggleTheme()
    expect(useThemeStore.getState().theme).toBe('dark')
  })

  it('setTheme applies the .dark class to <html> only for the dark theme', () => {
    useThemeStore.getState().setTheme('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)

    useThemeStore.getState().setTheme('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('persists the chosen theme to localStorage', () => {
    useThemeStore.getState().setTheme('light')
    expect(localStorage.getItem('projecthub-theme')).toBe('light')

    useThemeStore.getState().toggleTheme()
    expect(localStorage.getItem('projecthub-theme')).toBe('dark')
  })
})
