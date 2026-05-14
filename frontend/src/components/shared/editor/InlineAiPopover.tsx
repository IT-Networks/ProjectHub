import { useCallback, useEffect, useRef, useState } from 'react'
import { Sparkles, Wand2, Minimize2, Maximize2, Languages, X, Check, Loader2 } from 'lucide-react'
import type { Editor } from '@tiptap/react'
import { streamAiGenerate, type AiStreamRequest } from '@/lib/aiStream'
import { INLINE_AI_EVENT, type InlineAiTriggerDetail } from './inlineAi'
import { cn } from '@/lib/utils'

type Mode = AiStreamRequest['mode']

const ACTIONS: { mode: Mode; label: string; icon: typeof Sparkles }[] = [
  { mode: 'improve', label: 'Verbessern', icon: Wand2 },
  { mode: 'shorten', label: 'Kürzen', icon: Minimize2 },
  { mode: 'expand', label: 'Erweitern', icon: Maximize2 },
  { mode: 'fix_grammar', label: 'Grammatik', icon: Languages },
]

interface Props {
  editor: Editor
}

interface Anchor {
  top: number
  left: number
}

export function InlineAiPopover({ editor }: Props) {
  const [selection, setSelection] = useState<InlineAiTriggerDetail | null>(null)
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  const [mode, setMode] = useState<Mode | null>(null)
  const [result, setResult] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<InlineAiTriggerDetail>).detail
      if (!detail) return
      const coords = editor.view.coordsAtPos(detail.to)
      setAnchor({ top: coords.bottom + window.scrollY + 4, left: coords.left + window.scrollX })
      setSelection(detail)
      setMode(null)
      setResult('')
      setError(null)
    }
    window.addEventListener(INLINE_AI_EVENT, handler)
    return () => window.removeEventListener(INLINE_AI_EVENT, handler)
  }, [editor])

  const close = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setSelection(null)
    setAnchor(null)
    setMode(null)
    setResult('')
    setError(null)
    setStreaming(false)
  }, [])

  useEffect(() => {
    if (!selection) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        close()
      }
    }
    const onClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        if (streaming) return
        close()
      }
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onClick)
    }
  }, [selection, streaming, close])

  const runAction = async (m: Mode) => {
    if (!selection) return
    setMode(m)
    setResult('')
    setError(null)
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamAiGenerate(
        { mode: m, text: selection.text },
        (evt) => {
          if (evt.type === 'token') setResult((prev) => prev + evt.token)
          else if (evt.type === 'error') setError(evt.error)
        },
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError((e as Error).message || 'Fehler beim Streaming')
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  const accept = () => {
    if (!selection || !result.trim()) return
    editor
      .chain()
      .focus()
      .setTextSelection({ from: selection.from, to: selection.to })
      .insertContent(result.trim())
      .run()
    close()
  }

  if (!selection || !anchor) return null

  return (
    <div
      ref={rootRef}
      role="dialog"
      aria-label="KI-Aktion für markierten Text"
      className="fixed z-50 w-80 overflow-hidden rounded-lg border border-border bg-popover shadow-xl"
      style={{ top: anchor.top, left: anchor.left }}
    >
      <div className="flex items-center gap-2 border-b border-border bg-brand-subtle/40 px-3 py-2 text-xs">
        <Sparkles className="h-3.5 w-3.5 text-brand" aria-hidden />
        <span className="font-medium">KI-Aktion</span>
        <span className="ml-auto text-muted-foreground">Esc schließt</span>
      </div>

      {!mode && (
        <div className="grid grid-cols-2 gap-1 p-1.5">
          {ACTIONS.map(({ mode: m, label, icon: Icon }) => (
            <button
              key={m}
              type="button"
              onClick={() => runAction(m)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted/60"
            >
              <Icon className="h-3.5 w-3.5 text-muted-foreground" />
              {label}
            </button>
          ))}
        </div>
      )}

      {mode && (
        <div className="p-2">
          <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
            {streaming ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            <span>{ACTIONS.find((a) => a.mode === mode)?.label}</span>
          </div>

          <div
            className={cn(
              'max-h-48 overflow-y-auto rounded-md border border-border bg-muted/30 px-2 py-1.5 text-sm',
              !result && !error && 'text-muted-foreground italic',
            )}
          >
            {error ? (
              <span className="text-red-500">{error}</span>
            ) : result ? (
              result
            ) : streaming ? (
              'KI arbeitet…'
            ) : (
              ''
            )}
          </div>

          <div className="mt-2 flex items-center justify-end gap-1">
            <button
              type="button"
              onClick={close}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted/60"
            >
              <X className="h-3 w-3" /> Verwerfen
            </button>
            <button
              type="button"
              onClick={accept}
              disabled={streaming || !result.trim() || !!error}
              className="inline-flex items-center gap-1 rounded-md bg-brand px-2 py-1 text-xs font-medium text-brand-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              <Check className="h-3 w-3" /> Übernehmen
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
