import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface Shortcut {
  key: string
  label: string
  description: string
  category: 'navigation' | 'creation' | 'general'
}

const SHORTCUTS: Shortcut[] = [
  // Navigation
  { key: '1', label: 'Dashboard', description: 'Go to dashboard', category: 'navigation' },
  { key: '2', label: 'Projects', description: 'Go to projects', category: 'navigation' },
  { key: '3', label: 'Kanban', description: 'Go to kanban board', category: 'navigation' },
  { key: '4', label: 'Timeline', description: 'Go to timeline view', category: 'navigation' },
  { key: '5', label: 'Inbox', description: 'Go to inbox', category: 'navigation' },
  { key: '6', label: 'Queue', description: 'Go to todo queue', category: 'navigation' },
  { key: '7', label: 'Settings', description: 'Go to settings', category: 'navigation' },

  // Creation
  { key: 'n', label: 'New', description: 'Create new item (context-aware)', category: 'creation' },

  // General
  { key: 'Cmd/Ctrl + K', label: 'Command Search', description: 'Open search and command palette', category: 'general' },
  { key: '?', label: 'Help', description: 'Show keyboard shortcuts', category: 'general' },
  { key: 'Esc', label: 'Escape', description: 'Close modals or clear focus', category: 'general' },
]

const CATEGORIES = {
  navigation: 'Navigation',
  creation: 'Creation',
  general: 'General',
}

export function KeyboardShortcutsHelp() {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const handler = (e: CustomEvent) => {
      if (e.detail?.action === 'help') {
        setOpen(true)
      }
    }

    window.addEventListener('keyboard-shortcut', handler as EventListener)
    return () => window.removeEventListener('keyboard-shortcut', handler as EventListener)
  }, [])

  const filteredShortcuts = SHORTCUTS.filter(
    (s) =>
      s.label.toLowerCase().includes(search.toLowerCase()) ||
      s.description.toLowerCase().includes(search.toLowerCase()) ||
      s.key.toLowerCase().includes(search.toLowerCase())
  )

  const shortcutsByCategory = Object.entries(CATEGORIES).reduce(
    (acc, [key, label]) => {
      acc[key as keyof typeof CATEGORIES] = filteredShortcuts.filter(
        (s) => s.category === key
      )
      return acc
    },
    {} as Record<keyof typeof CATEGORIES, Shortcut[]>
  )

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Keyboard Shortcuts</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <Input
            placeholder="Search shortcuts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />

          {Object.entries(shortcutsByCategory).map(
            ([categoryKey, shortcuts]) =>
              shortcuts.length > 0 && (
                <div key={categoryKey} className="space-y-2">
                  <h3 className="text-sm font-semibold text-muted-foreground">
                    {CATEGORIES[categoryKey as keyof typeof CATEGORIES]}
                  </h3>
                  <div className="space-y-1">
                    {shortcuts.map((shortcut) => (
                      <div
                        key={shortcut.key}
                        className="flex items-center justify-between rounded-lg p-2 hover:bg-muted/50 transition-colors"
                      >
                        <div className="flex-1">
                          <p className="text-sm font-medium">{shortcut.label}</p>
                          <p className="text-xs text-muted-foreground">
                            {shortcut.description}
                          </p>
                        </div>
                        <kbd
                          className={cn(
                            'ml-4 px-2 py-1 rounded text-xs font-mono',
                            'bg-muted border border-border',
                            'text-foreground whitespace-nowrap'
                          )}
                        >
                          {shortcut.key}
                        </kbd>
                      </div>
                    ))}
                  </div>
                </div>
              )
          )}

          {filteredShortcuts.length === 0 && (
            <div className="text-center py-8 text-sm text-muted-foreground">
              No shortcuts match "{search}"
            </div>
          )}
        </div>

        <div className="text-xs text-muted-foreground pt-4 border-t">
          <p>💡 Tip: Press <kbd className="px-1 py-0.5 bg-muted rounded text-xs">?</kbd> anytime to open this help</p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
