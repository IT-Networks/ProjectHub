import { Extension } from '@tiptap/core'

export const INLINE_AI_EVENT = 'projecthub:inline-ai-trigger'

export interface InlineAiTriggerDetail {
  from: number
  to: number
  text: string
}

export const InlineAi = Extension.create({
  name: 'inlineAi',

  addKeyboardShortcuts() {
    return {
      'Mod-j': () => {
        const { from, to } = this.editor.state.selection
        if (from === to) return false
        const text = this.editor.state.doc.textBetween(from, to, ' ')
        if (!text.trim()) return false
        window.dispatchEvent(
          new CustomEvent<InlineAiTriggerDetail>(INLINE_AI_EVENT, {
            detail: { from, to, text },
          }),
        )
        return true
      },
    }
  },
})
