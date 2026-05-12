import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'
import { Layers, CheckSquare, FileText, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export type MentionType = 'project' | 'todo' | 'note'

export interface MentionItem {
  id: string
  label: string
  type: MentionType
  project_id?: string | null
  href: string
}

interface Props {
  items: MentionItem[]
  loading: boolean
  command: (item: MentionItem) => void
}

export interface MenuHandle {
  onKeyDown: (e: { event: KeyboardEvent }) => boolean
}

const ICONS: Record<MentionType, typeof Layers> = {
  project: Layers,
  todo: CheckSquare,
  note: FileText,
}

const TYPE_LABEL: Record<MentionType, string> = {
  project: 'Projekt',
  todo: 'Todo',
  note: 'Notiz',
}

export const MentionMenu = forwardRef<MenuHandle, Props>(function MentionMenu(
  { items, loading, command },
  ref,
) {
  const [selected, setSelected] = useState(0)

  useEffect(() => setSelected(0), [items])

  useImperativeHandle(ref, () => ({
    onKeyDown: ({ event }) => {
      if (items.length === 0) return false
      if (event.key === 'ArrowUp') {
        setSelected((i) => (i - 1 + items.length) % items.length)
        return true
      }
      if (event.key === 'ArrowDown') {
        setSelected((i) => (i + 1) % items.length)
        return true
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        command(items[selected])
        return true
      }
      return false
    },
  }))

  return (
    <div className="w-72 overflow-hidden rounded-lg border border-border bg-popover shadow-xl">
      <div className="max-h-72 overflow-y-auto p-1">
        {loading && items.length === 0 && (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Suche…
          </div>
        )}
        {!loading && items.length === 0 && (
          <div className="px-2 py-3 text-xs text-muted-foreground">Keine Treffer</div>
        )}
        {items.map((item, idx) => {
          const Icon = ICONS[item.type]
          const active = idx === selected
          return (
            <button
              key={`${item.type}-${item.id}`}
              type="button"
              onMouseEnter={() => setSelected(idx)}
              onMouseDown={(e) => {
                e.preventDefault()
                command(item)
              }}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
                active ? 'bg-brand-subtle text-foreground' : 'text-foreground hover:bg-muted/50',
              )}
            >
              <Icon className={cn('h-3.5 w-3.5 shrink-0', active ? 'text-brand' : 'text-muted-foreground')} />
              <span className="flex-1 truncate text-sm">{item.label}</span>
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {TYPE_LABEL[item.type]}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
})
