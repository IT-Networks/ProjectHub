import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Command } from 'cmdk'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface SearchResult {
  id: string
  type: 'project' | 'todo' | 'note' | 'message'
  match: string
  title?: string
  name?: string
  subject?: string
  project_id?: string
  status?: string
}

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const navigate = useNavigate()

  // Ctrl+K to toggle
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Search on query change
  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      return
    }
    const timer = setTimeout(async () => {
      try {
        const data = await api.get<{
          projects: SearchResult[]
          todos: SearchResult[]
          notes: SearchResult[]
          messages: SearchResult[]
        }>(`/search?q=${encodeURIComponent(query)}`)
        setResults([
          ...data.projects,
          ...data.todos,
          ...data.notes,
          ...data.messages,
        ])
      } catch {
        setResults([])
      }
    }, 200)
    return () => clearTimeout(timer)
  }, [query])

  const handleSelect = useCallback((item: SearchResult) => {
    setOpen(false)
    setQuery('')
    switch (item.type) {
      case 'project':
        navigate(`/projekte/${item.id}`)
        break
      case 'todo':
        if (item.project_id) navigate(`/projekte/${item.project_id}`)
        else navigate('/kanban')
        break
      case 'note':
        if (item.project_id) navigate(`/projekte/${item.project_id}`)
        break
      case 'message':
        navigate('/inbox')
        break
    }
  }, [navigate])

  if (!open) return null

  const TYPE_LABELS: Record<string, string> = {
    project: 'Projekt',
    todo: 'Todo',
    note: 'Notiz',
    message: 'Nachricht',
  }

  const TYPE_ICONS: Record<string, string> = {
    project: '▦',
    todo: '☐',
    note: '📝',
    message: '✉',
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />

      {/* Command dialog */}
      <div className="relative w-full max-w-lg">
        <Command className="rounded-lg border border-border bg-popover shadow-2xl" shouldFilter={false}>
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Suche in Projekten, Todos, Notizen..."
            className="h-12 w-full border-b border-border bg-transparent px-4 text-sm outline-none placeholder:text-muted-foreground"
            autoFocus
          />
          <Command.List className="max-h-[300px] overflow-y-auto p-2">
            {query && results.length === 0 && (
              <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
                Keine Ergebnisse
              </Command.Empty>
            )}

            {!query && (
              <div className="py-4 text-center text-xs text-muted-foreground">
                <p>Tippe um zu suchen</p>
                <div className="mt-3 flex justify-center gap-4">
                  <span><kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-xs">1</kbd> Dashboard</span>
                  <span><kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-xs">2</kbd> Projekte</span>
                  <span><kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-xs">3</kbd> Kanban</span>
                </div>
              </div>
            )}

            {results.map((item) => (
              <Command.Item
                key={`${item.type}-${item.id}`}
                value={`${item.type}-${item.id}`}
                onSelect={() => handleSelect(item)}
                className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm aria-selected:bg-accent"
              >
                <span className="w-5 text-center text-muted-foreground">{TYPE_ICONS[item.type]}</span>
                <span className="flex-1 truncate">{item.name || item.title || item.subject || item.match}</span>
                <span className="text-xs text-muted-foreground">{TYPE_LABELS[item.type]}</span>
              </Command.Item>
            ))}
          </Command.List>
        </Command>
      </div>
    </div>
  )
}
