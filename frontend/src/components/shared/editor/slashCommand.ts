import { Extension, type Range } from '@tiptap/core'
import type { Editor } from '@tiptap/react'
import Suggestion, { type SuggestionOptions } from '@tiptap/suggestion'
import { PluginKey } from '@tiptap/pm/state'
import tippy, { type Instance as TippyInstance } from 'tippy.js'
import { ReactRenderer } from '@tiptap/react'
import { SlashCommandMenu, SLASH_ITEMS, type SlashItem, type MenuHandle } from './SlashCommandMenu'

const slashSuggestionKey = new PluginKey('slashSuggestion')

type SlashOptions = {
  suggestion: Omit<SuggestionOptions<SlashItem>, 'editor'>
}

export const SlashCommand = Extension.create<SlashOptions>({
  name: 'slashCommand',

  addOptions() {
    return {
      suggestion: {
        char: '/',
        startOfLine: false,
        allowSpaces: false,
        command: ({ editor, range, props }) => {
          props.command({ editor: editor as Editor, range })
        },
      },
    }
  },

  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        pluginKey: slashSuggestionKey,
        ...this.options.suggestion,
      }),
    ]
  },
})

export function createSlashSuggestion(): SlashOptions['suggestion'] {
  return {
    char: '/',
    startOfLine: false,
    allowSpaces: false,
    items: ({ query }: { query: string }): SlashItem[] => {
      const q = query.toLowerCase().trim()
      if (!q) return SLASH_ITEMS
      return SLASH_ITEMS.filter(
        (item) =>
          item.title.toLowerCase().includes(q) ||
          item.keywords.some((k) => k.includes(q)),
      )
    },
    command: ({ editor, range, props }: { editor: Editor; range: Range; props: SlashItem }) => {
      void props.command({ editor, range })
    },
    render: () => {
      let component: ReactRenderer<MenuHandle> | null = null
      let popup: TippyInstance[] = []

      return {
        onStart: (props: { editor: Editor; clientRect?: (() => DOMRect | null) | null }) => {
          component = new ReactRenderer(SlashCommandMenu, {
            props,
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
        onUpdate(props: { clientRect?: (() => DOMRect | null) | null; items: SlashItem[] }) {
          component?.updateProps(props)
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
