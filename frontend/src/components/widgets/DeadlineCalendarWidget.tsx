import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { Todo, Note } from '@/lib/types'

interface DeadlineItem {
  id: string
  title: string
  deadline: string
  type: 'todo' | 'note'
  isOverdue: boolean
  daysLeft: number
}

export function DeadlineCalendarWidget() {
  const [items, setItems] = useState<DeadlineItem[]>([])

  useEffect(() => {
    const load = async () => {
      const now = new Date()
      const nextWeek = new Date(now.getTime() + 14 * 24 * 60 * 60 * 1000)
      const results: DeadlineItem[] = []

      try {
        const todos = await api.get<Todo[]>('/todos')
        for (const t of todos) {
          if (t.deadline && t.status !== 'done') {
            const dl = new Date(t.deadline)
            if (dl <= nextWeek) {
              results.push({
                id: t.id,
                title: t.title,
                deadline: t.deadline,
                type: 'todo',
                isOverdue: dl < now,
                daysLeft: Math.ceil((dl.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)),
              })
            }
          }
        }
      } catch { /* offline */ }

      try {
        const notes = await api.get<Note[]>('/notes')
        for (const n of notes) {
          if (n.deadline) {
            const dl = new Date(n.deadline)
            if (dl <= nextWeek) {
              results.push({
                id: n.id,
                title: n.title || 'Notiz',
                deadline: n.deadline,
                type: 'note',
                isOverdue: dl < now,
                daysLeft: Math.ceil((dl.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)),
              })
            }
          }
        }
      } catch { /* offline */ }

      results.sort((a, b) => new Date(a.deadline).getTime() - new Date(b.deadline).getTime())
      setItems(results)
    }
    load()
  }, [])

  return (
    <div className="space-y-2">
      {items.length === 0 && (
        <p className="py-4 text-center text-xs text-muted-foreground">Keine Fristen in den nächsten 14 Tagen</p>
      )}
      {items.map((item) => (
        <div key={`${item.type}-${item.id}`} className="flex items-center justify-between rounded border border-border px-3 py-2">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-xs">
              {item.type === 'todo' ? 'Todo' : 'Notiz'}
            </Badge>
            <span className="text-sm">{item.title}</span>
          </div>
          <span className={cn('text-xs font-medium', item.isOverdue ? 'text-red-400' : item.daysLeft <= 3 ? 'text-yellow-400' : 'text-muted-foreground')}>
            {item.isOverdue
              ? `${Math.abs(item.daysLeft)} Tage überfällig`
              : item.daysLeft === 0
                ? 'Heute'
                : `${item.daysLeft} Tage`}
          </span>
        </div>
      ))}
    </div>
  )
}
