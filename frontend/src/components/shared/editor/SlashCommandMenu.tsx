import { forwardRef, useEffect, useImperativeHandle, useMemo, useState, type ReactElement } from 'react'
import {
  Heading2,
  Heading3,
  List,
  ListOrdered,
  ListTodo,
  Code2,
  Quote,
  Minus,
  Sparkles,
  Wand2,
  type LucideIcon,
} from 'lucide-react'
import type { Editor, Range } from '@tiptap/react'
import { streamAiGenerate } from '@/lib/aiStream'
import { cn } from '@/lib/utils'

export interface SlashItem {
  id: string
  title: string
  description: string
  section: 'Basis' | 'AI'
  icon: LucideIcon
  keywords: string[]
  command: (args: { editor: Editor; range: Range }) => void | Promise<void>
}

export const SLASH_ITEMS: SlashItem[] = [
  {
    id: 'h2',
    title: 'Überschrift 2',
    description: 'Mittlere Abschnittsüberschrift',
    section: 'Basis',
    icon: Heading2,
    keywords: ['h2', 'heading', 'überschrift'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).setNode('heading', { level: 2 }).run(),
  },
  {
    id: 'h3',
    title: 'Überschrift 3',
    description: 'Kleinere Überschrift',
    section: 'Basis',
    icon: Heading3,
    keywords: ['h3', 'heading', 'überschrift'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).setNode('heading', { level: 3 }).run(),
  },
  {
    id: 'bulletList',
    title: 'Aufzählung',
    description: 'Einfache ungeordnete Liste',
    section: 'Basis',
    icon: List,
    keywords: ['list', 'liste', 'aufzählung', 'bullet'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).toggleBulletList().run(),
  },
  {
    id: 'orderedList',
    title: 'Nummerierte Liste',
    description: 'Nummerierte Liste',
    section: 'Basis',
    icon: ListOrdered,
    keywords: ['ol', 'nummer', 'ordered'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).toggleOrderedList().run(),
  },
  {
    id: 'taskList',
    title: 'Aufgabenliste',
    description: 'Checkboxen für Todos',
    section: 'Basis',
    icon: ListTodo,
    keywords: ['task', 'todo', 'aufgabe', 'checkbox'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).toggleTaskList().run(),
  },
  {
    id: 'codeBlock',
    title: 'Code-Block',
    description: 'Formatierter Codeabschnitt',
    section: 'Basis',
    icon: Code2,
    keywords: ['code', 'pre'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).toggleCodeBlock().run(),
  },
  {
    id: 'blockquote',
    title: 'Zitat',
    description: 'Eingerückter Zitatblock',
    section: 'Basis',
    icon: Quote,
    keywords: ['quote', 'zitat'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).toggleBlockquote().run(),
  },
  {
    id: 'hr',
    title: 'Trennlinie',
    description: 'Horizontale Linie',
    section: 'Basis',
    icon: Minus,
    keywords: ['hr', 'line', 'trenn'],
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).setHorizontalRule().run(),
  },
  {
    id: 'ai-continue',
    title: 'KI · Weiterschreiben',
    description: 'Setzt den Text mit ein paar Sätzen fort',
    section: 'AI',
    icon: Sparkles,
    keywords: ['ai', 'ki', 'weiter', 'continue'],
    command: async ({ editor, range }) => {
      editor.chain().focus().deleteRange(range).run()
      await runAiAction(editor, 'continue')
    },
  },
  {
    id: 'ai-summarize',
    title: 'KI · Zusammenfassen',
    description: 'Ersetzt den Text durch eine Zusammenfassung',
    section: 'AI',
    icon: Wand2,
    keywords: ['ai', 'ki', 'summarize', 'zusammen'],
    command: async ({ editor, range }) => {
      editor.chain().focus().deleteRange(range).run()
      await runAiAction(editor, 'summarize', { replace: true })
    },
  },
]

async function runAiAction(editor: Editor, mode: 'continue' | 'summarize', opts: { replace?: boolean } = {}) {
  const plainText = editor.getText()
  if (!plainText.trim()) {
    editor.chain().focus().insertContent('(Bitte schreibe zuerst Text, den die KI verarbeiten kann.)').run()
    return
  }

  editor.setEditable(false)
  try {
    if (opts.replace) {
      editor.commands.setContent('', { emitUpdate: false })
    } else {
      editor.chain().focus('end').insertContent('\n\n').run()
    }

    let received = ''
    await streamAiGenerate({ mode, text: plainText }, (evt) => {
      if (evt.type === 'token') {
        received += evt.token
        editor.chain().focus('end').insertContent(evt.token).run()
      } else if (evt.type === 'error') {
        editor.chain().focus('end').insertContent(`\n_(KI-Fehler: ${evt.error})_`).run()
      }
    })

    if (!received.trim() && !opts.replace) {
      editor.chain().focus('end').insertContent('_(KI lieferte keinen Text)_').run()
    }
  } catch (e) {
    const message = e instanceof Error ? e.message : 'Unbekannter Fehler'
    editor.chain().focus('end').insertContent(`\n\n_(KI-Anfrage fehlgeschlagen: ${message})_`).run()
  } finally {
    editor.setEditable(true)
  }
}

interface MenuProps {
  items: SlashItem[]
  command: (item: SlashItem) => void
}

export interface MenuHandle {
  onKeyDown: (e: { event: KeyboardEvent }) => boolean
}

export const SlashCommandMenu = forwardRef<MenuHandle, MenuProps>(function SlashCommandMenu(
  { items, command },
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
      if (event.key === 'Enter') {
        command(items[selected])
        return true
      }
      return false
    },
  }))

  const grouped = useMemo(() => {
    const map = new Map<SlashItem['section'], SlashItem[]>()
    for (const item of items) {
      const arr = map.get(item.section) ?? []
      arr.push(item)
      map.set(item.section, arr)
    }
    return Array.from(map.entries())
  }, [items])

  if (items.length === 0) {
    return (
      <div className="w-64 rounded-lg border border-border bg-popover p-3 text-xs text-muted-foreground shadow-lg">
        Keine Treffer
      </div>
    )
  }

  let idx = 0
  const sections: ReactElement[] = []
  for (const [section, arr] of grouped) {
    sections.push(
      <div key={section} className="py-1">
        <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {section}
        </div>
        {arr.map((item) => {
          const currentIdx = idx++
          const active = currentIdx === selected
          const Icon = item.icon
          return (
            <button
              key={item.id}
              type="button"
              onMouseEnter={() => setSelected(currentIdx)}
              onMouseDown={(e) => {
                e.preventDefault()
                command(item)
              }}
              className={cn(
                'flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left transition-colors',
                active ? 'bg-brand-subtle text-foreground' : 'text-foreground hover:bg-muted/50',
              )}
            >
              <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', active ? 'text-brand' : 'text-muted-foreground')} />
              <span className="flex-1 min-w-0">
                <span className="block truncate text-sm font-medium">{item.title}</span>
                <span className="block truncate text-xs text-muted-foreground">{item.description}</span>
              </span>
            </button>
          )
        })}
      </div>,
    )
  }

  return (
    <div className="w-72 overflow-hidden rounded-lg border border-border bg-popover shadow-xl">
      <div className="max-h-80 overflow-y-auto px-1 py-0.5">{sections}</div>
    </div>
  )
})
