import { Extension, type Range } from '@tiptap/core'
import type { Editor } from '@tiptap/react'
import Suggestion, { type SuggestionOptions } from '@tiptap/suggestion'
import { PluginKey } from '@tiptap/pm/state'
import tippy, { type Instance as TippyInstance } from 'tippy.js'
import { ReactRenderer } from '@tiptap/react'
import { api } from '@/lib/api'
import { MentionMenu, type MentionItem, type MenuHandle } from './MentionMenu'

const mentionSuggestionKey = new PluginKey('mentionSuggestion')

interface SearchResponse {
  projects: Array<{ id: string; name: string; match?: string }>
  todos: Array<{ id: string; title: string; project_id?: string | null; match?: string }>
  notes: Array<{ id: string; title: string; project_id?: string | null; match?: string }>
}

type MentionOptions = {
  suggestion: Omit<SuggestionOptions<MentionItem>, 'editor'>
}

export const Mention = Extension.create<MentionOptions>({
  name: 'mention',

  addOptions() {
    return {
      suggestion: {
        char: '@',
        startOfLine: false,
        allowSpaces: false,
      },
    }
  },

  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        pluginKey: mentionSuggestionKey,
        ...this.options.suggestion,
      }),
    ]
  },
})

async function fetchMentions(query: string, limit = 8): Promise<MentionItem[]> {
  if (!query.trim()) return []
  try {
    const data = await api.get<SearchResponse>(`/search?q=${encodeURIComponent(query)}`)
    const items: MentionItem[] = []

    for (const p of data.projects ?? []) {
      items.push({
        id: p.id,
        label: p.name,
        type: 'project',
        href: `/projekte/${p.id}`,
      })
    }
    for (const t of data.todos ?? []) {
      items.push({
        id: t.id,
        label: t.title,
        type: 'todo',
        project_id: t.project_id ?? null,
        href: t.project_id ? `/projekte/${t.project_id}` : '/kanban',
      })
    }
    for (const n of data.notes ?? []) {
      items.push({
        id: n.id,
        label: n.title || 'Notiz',
        type: 'note',
        project_id: n.project_id ?? null,
        href: n.project_id ? `/projekte/${n.project_id}` : '/',
      })
    }

    return items.slice(0, limit)
  } catch {
    return []
  }
}

export function createMentionSuggestion(): MentionOptions['suggestion'] {
  let itemsCache: MentionItem[] = []

  return {
    char: '@',
    startOfLine: false,
    allowSpaces: false,
    items: async ({ query }: { query: string }): Promise<MentionItem[]> => {
      const list = await fetchMentions(query)
      itemsCache = list
      return list
    },
    command: ({ editor, range, props }: { editor: Editor; range: Range; props: MentionItem }) => {
      editor
        .chain()
        .focus()
        .deleteRange(range)
        .insertContent([
          {
            type: 'text',
            text: `@${props.label}`,
            marks: [{ type: 'link', attrs: { href: props.href, target: '_self' } }],
          },
          { type: 'text', text: ' ' },
        ])
        .run()
    },
    render: () => {
      let component: ReactRenderer<MenuHandle> | null = null
      let popup: TippyInstance[] = []
      let loading = false

      const updateMenu = () => {
        component?.updateProps({ items: itemsCache, loading })
      }

      return {
        onStart: (props: {
          editor: Editor
          clientRect?: (() => DOMRect | null) | null
          items: MentionItem[]
          command: (item: MentionItem) => void
        }) => {
          loading = true
          component = new ReactRenderer(MentionMenu, {
            props: { items: props.items, loading, command: props.command },
            editor: props.editor,
          })
          const rect = props.clientRect?.()
          if (!rect) return
          popup = tippy('body', {
            getReferenceClientRect: () => rect,
            appendTo: () => document.body,
            content: component.element,
            showOnCreate: true,
            interactive: true,
            trigger: 'manual',
            placement: 'bottom-start',
            arrow: false,
          })
        },
        onUpdate(props: {
          clientRect?: (() => DOMRect | null) | null
          items: MentionItem[]
          command: (item: MentionItem) => void
        }) {
          loading = false
          component?.updateProps({
            items: props.items,
            loading: false,
            command: props.command,
          })
          void updateMenu
          const rect = props.clientRect?.()
          if (rect) popup[0]?.setProps({ getReferenceClientRect: () => rect })
        },
        onKeyDown(props: { event: KeyboardEvent }) {
          if (props.event.key === 'Escape') {
            popup[0]?.hide()
            return true
          }
          return component?.ref?.onKeyDown(props) ?? false
        },
        onExit() {
          popup[0]?.destroy()
          component?.destroy()
          popup = []
          component = null
        },
      }
    },
  }
}
