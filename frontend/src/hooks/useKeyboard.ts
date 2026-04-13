import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

const ROUTES: Record<string, string> = {
  '1': '/',
  '2': '/projekte',
  '3': '/kanban',
  '4': '/inbox',
  '5': '/queue',
}

export function useKeyboardShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't trigger in input/textarea
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) {
        return
      }

      // Number keys for navigation
      if (ROUTES[e.key] && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault()
        navigate(ROUTES[e.key])
        return
      }

      // ? for help
      if (e.key === '?' && !e.ctrlKey) {
        e.preventDefault()
        // Could show a shortcut overlay in the future
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])
}
