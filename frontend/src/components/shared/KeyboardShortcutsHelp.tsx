import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface Shortcut {
  key: string
  label: string
  description: string
  category: 'navigation' | 'creation' | 'timeline' | 'kanban' | 'general'
}

const SHORTCUTS: Shortcut[] = [
  // Navigation
  { key: '1', label: 'Dashboard', description: 'Zum Dashboard', category: 'navigation' },
  { key: '2', label: 'Projekte', description: 'Zu den Projekten', category: 'navigation' },
  { key: '3', label: 'Kanban', description: 'Zum Kanban-Board', category: 'navigation' },
  { key: '4', label: 'Timeline', description: 'Zur Timeline-Ansicht', category: 'navigation' },
  { key: '5', label: 'Inbox', description: 'Zum Posteingang', category: 'navigation' },
  { key: '6', label: 'Queue', description: 'Zur Todo-Queue', category: 'navigation' },
  { key: '7', label: 'Einstellungen', description: 'Zu den Einstellungen', category: 'navigation' },

  // Creation
  { key: '/', label: 'Quick-Add', description: 'Todo-Eingabe oben fokussieren', category: 'creation' },
  { key: 'n', label: 'Neu', description: 'Neues Item anlegen (kontextabhängig)', category: 'creation' },

  // Timeline
  { key: '1 / 2 / 3', label: 'Ansicht', description: 'Schedule / Calendar / Gantt umschalten', category: 'timeline' },
  { key: 'h / l', label: 'Cursor', description: 'Cursor eine Einheit zurück/vor', category: 'timeline' },
  { key: 'j / k', label: 'Buckets', description: 'Zwischen Schedule-Buckets scrollen', category: 'timeline' },
  { key: '.', label: 'Heute', description: 'Cursor auf heute setzen', category: 'timeline' },
  { key: 'c', label: 'Erledigte', description: 'Abgeschlossene Items ein-/ausblenden', category: 'timeline' },

  // Kanban
  { key: '[ / ]', label: 'Dichte', description: 'Karten-Dichte compact ↔ comfortable ↔ spacious', category: 'kanban' },
  { key: 'Cmd/Ctrl + A', label: 'Alle auswählen', description: 'Alle Karten markieren', category: 'kanban' },
  { key: 'Shift + Klick', label: 'Mehrfachauswahl', description: 'Range-Selection zwischen Karten', category: 'kanban' },

  // General
  { key: 'Cmd/Ctrl + K', label: 'Suche', description: 'Command-Palette öffnen', category: 'general' },
  { key: '?', label: 'Hilfe', description: 'Diese Übersicht anzeigen', category: 'general' },
  { key: 'Esc', label: 'Abbrechen', description: 'Dialoge schließen, Auswahl aufheben', category: 'general' },
]

const CATEGORIES = {
  navigation: 'Navigation',
  creation: 'Erstellen',
  timeline: 'Timeline',
  kanban: 'Kanban',
  general: 'Allgemein',
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
    (acc, [key]) => {
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
