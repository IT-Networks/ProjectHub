import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useProjectStore } from '@/stores/projectStore'
import { useTodoStore } from '@/stores/todoStore'

const ROUTES: Record<string, string> = {
  '1': '/',
  '2': '/projekte',
  '3': '/kanban',
  '4': '/timeline',
  '5': '/inbox',
  '6': '/queue',
  '7': '/einstellungen',
}

interface KeyboardAction {
  key: string
  label: string
  description: string
  handler: () => void
}

export function useKeyboardShortcuts() {
  const navigate = useNavigate()
  const location = useLocation()
  const createProject = useProjectStore((s) => s.createProject)
  const createTodo = useTodoStore((s) => s.createTodo)

  // Get current page context
  const getCurrentPageContext = () => {
    const path = location.pathname
    if (path === '/') return 'dashboard'
    if (path === '/projekte') return 'projects'
    if (path.startsWith('/projekte/')) return 'project-detail'
    if (path === '/kanban') return 'kanban'
    if (path === '/timeline') return 'timeline'
    if (path === '/inbox') return 'inbox'
    if (path === '/queue') return 'queue'
    if (path === '/einstellungen') return 'settings'
    return 'unknown'
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't trigger in input/textarea unless specific keys
      const tag = (e.target as HTMLElement)?.tagName
      const isFormElement = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable

      // Allow Escape, Enter, and number keys even in inputs
      if (isFormElement && e.key !== 'Escape' && e.key !== 'Enter' && !['1', '2', '3', '4', '5', '6', '7'].includes(e.key)) {
        return
      }

      // Number keys for navigation (1-7)
      if (ROUTES[e.key] && !e.ctrlKey && !e.metaKey && !e.altKey && !isFormElement) {
        e.preventDefault()
        navigate(ROUTES[e.key])
        return
      }

      // n = New (context-aware)
      if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey && !isFormElement) {
        e.preventDefault()
        const context = getCurrentPageContext()

        // Dispatch custom event for page to handle new action
        window.dispatchEvent(
          new CustomEvent('keyboard-shortcut', {
            detail: { action: 'new', context },
          })
        )
        return
      }

      // ? = Help
      if (e.key === '?' && !e.ctrlKey && !e.shiftKey && !isFormElement) {
        e.preventDefault()
        window.dispatchEvent(
          new CustomEvent('keyboard-shortcut', {
            detail: { action: 'help' },
          })
        )
        return
      }

      // h = Help (alternative)
      if (e.key === 'h' && e.ctrlKey && !e.metaKey && !e.altKey && !isFormElement) {
        e.preventDefault()
        window.dispatchEvent(
          new CustomEvent('keyboard-shortcut', {
            detail: { action: 'help' },
          })
        )
        return
      }

      // Escape to clear focus
      if (e.key === 'Escape' && isFormElement) {
        e.preventDefault()
        (e.target as HTMLElement).blur()
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate, location])
}

// Helper hook to listen for keyboard shortcuts
export function useKeyboardAction(callback: (action: string, context: string) => void) {
  useEffect(() => {
    const handler = (e: CustomEvent) => {
      const { action, context } = e.detail
      callback(action, context)
    }

    window.addEventListener('keyboard-shortcut', handler as EventListener)
    return () => window.removeEventListener('keyboard-shortcut', handler as EventListener)
  }, [callback])
}
