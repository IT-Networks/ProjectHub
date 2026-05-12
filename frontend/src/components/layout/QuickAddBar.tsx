import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useParams } from 'react-router-dom'
import { Sparkles, Loader2, CornerDownLeft, SlidersHorizontal } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useTodoStore } from '@/stores/todoStore'
import { useProjectStore } from '@/stores/projectStore'
import { useToast, ToastContainer } from '@/components/shared/Toast'
import { QuickAddPreviewDialog, type QuickAddDraft } from './QuickAddPreviewDialog'
import { cn } from '@/lib/utils'

interface ParseTodoResponse {
  title: string
  description: string | null
  priority: 'high' | 'medium' | 'low'
  deadline: string | null
  tags: string[]
  assignee_hint: string | null
  project_id: string | null
  confidence: number
  used_fallback: boolean
}

const LOW_CONFIDENCE_THRESHOLD = 0.5

export function QuickAddBar() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [draft, setDraft] = useState<QuickAddDraft | null>(null)
  const { id: urlProjectId } = useParams<{ id: string }>()
  const createTodo = useTodoStore((s) => s.createTodo)
  const projects = useProjectStore((s) => s.projects)
  const { toasts, removeToast, success, error } = useToast()

  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.key !== '/') return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const target = e.target as HTMLElement | null
      const tag = target?.tagName
      const isFormElement =
        tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable
      if (isFormElement) return
      // Don't hijack `/` while a modal/sheet/popover or listbox is open
      if (target?.closest('[role="dialog"], [role="menu"], [role="listbox"], .ProseMirror')) return
      if (document.querySelector('[role="dialog"][data-state="open"]')) return
      e.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const resolveProjectId = (aiMatch: string | null): string | null => {
    // Priorität: URL-Kontext > AI-Match > null
    if (urlProjectId) return urlProjectId
    if (aiMatch && projects.some((p) => p.id === aiMatch)) return aiMatch
    return null
  }

  const submit = async (opts: { forceModal?: boolean } = {}) => {
    const prompt = value.trim()
    if (!prompt || loading) return

    setLoading(true)
    try {
      const parsed = await api.post<ParseTodoResponse>('/ai/parse-todo', {
        prompt,
        context: {
          current_project_id: urlProjectId ?? null,
          now: new Date().toISOString(),
          available_projects: projects.map((p) => ({ id: p.id, name: p.name })),
        },
      })

      const resolved = resolveProjectId(parsed.project_id)
      const lowConf = parsed.confidence < LOW_CONFIDENCE_THRESHOLD || parsed.used_fallback
      if (lowConf || opts.forceModal) {
        setDraft({
          prompt,
          title: parsed.title,
          description: parsed.description ?? '',
          priority: parsed.priority,
          deadline: parsed.deadline,
          tags: parsed.tags,
          project_id: resolved,
          confidence: parsed.confidence,
          used_fallback: parsed.used_fallback,
        })
        inputRef.current?.blur()
        return
      }

      await createTodo({
        title: parsed.title,
        description: parsed.description ?? undefined,
        project_id: resolved,
        priority: parsed.priority,
        deadline: parsed.deadline,
        tags: parsed.tags,
      })

      setValue('')
      inputRef.current?.blur()
      success('Todo erstellt', { description: parsed.title })
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : (e as Error).message
      error('Konnte nicht erstellt werden', { description: msg })
    } finally {
      setLoading(false)
    }
  }

  const confirmDraft = async (patch: {
    title: string
    description: string
    priority: 'high' | 'medium' | 'low'
    deadline: string | null
    tags: string[]
    project_id: string | null
  }) => {
    try {
      await createTodo({
        title: patch.title,
        description: patch.description || undefined,
        project_id: patch.project_id,
        priority: patch.priority,
        deadline: patch.deadline,
        tags: patch.tags,
      })
      success('Todo erstellt', { description: patch.title })
      setDraft(null)
      setValue('')
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : (e as Error).message
      error('Konnte nicht erstellt werden', { description: msg })
    }
  }


  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setValue('')
      inputRef.current?.blur()
    }
  }

  return (
    <>
      <div
        className={cn(
          'flex h-9 items-center gap-2 rounded-md border bg-background pl-2.5 pr-1.5 transition-colors',
          'focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/20',
          loading ? 'border-brand/40' : 'border-input',
        )}
      >
        <Sparkles className="h-4 w-4 shrink-0 text-brand" aria-hidden />
        <input
          ref={inputRef}
          type="text"
          value={value}
          disabled={loading}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Todo hinzufügen — z. B. „PR morgen reviewen, Label frontend"
          className="h-full w-72 min-w-[200px] bg-transparent text-sm outline-none placeholder:text-muted-foreground/70 disabled:opacity-60"
          aria-label="Todo per KI erstellen"
        />
        {loading ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-brand" aria-hidden />
        ) : (
          <>
            <button
              type="button"
              onClick={() => submit({ forceModal: true })}
              disabled={!value.trim()}
              title="Details vor dem Erstellen prüfen"
              aria-label="Details bearbeiten"
              className={cn(
                'shrink-0 rounded p-1 text-muted-foreground transition-colors',
                'hover:bg-muted hover:text-foreground',
                'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground',
              )}
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
            </button>
            <kbd
              aria-hidden
              className={cn(
                'hidden shrink-0 items-center gap-0.5 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline-flex',
                value.trim() ? 'opacity-100' : 'opacity-50',
              )}
            >
              <CornerDownLeft className="h-2.5 w-2.5" />
            </kbd>
          </>
        )}
      </div>
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
      <QuickAddPreviewDialog
        open={draft !== null}
        draft={draft}
        projects={projects}
        onCancel={() => setDraft(null)}
        onSave={confirmDraft}
      />
    </>
  )
}
