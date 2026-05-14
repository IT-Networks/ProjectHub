import { useState } from 'react'
import { Sparkles, AlertTriangle } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { Priority, ProjectListItem } from '@/lib/types'

export interface QuickAddDraft {
  prompt: string
  title: string
  description: string
  priority: Priority
  deadline: string | null
  tags: string[]
  project_id: string | null
  confidence: number
  used_fallback: boolean
}

interface Props {
  open: boolean
  draft: QuickAddDraft | null
  projects: readonly ProjectListItem[]
  onCancel: () => void
  onSave: (patch: { title: string; description: string; priority: Priority; deadline: string | null; tags: string[]; project_id: string | null }) => Promise<void>
}

const NO_PROJECT = '__none__'

const PRIORITY_OPTS: { value: Priority; label: string }[] = [
  { value: 'high', label: 'Hoch' },
  { value: 'medium', label: 'Mittel' },
  { value: 'low', label: 'Niedrig' },
]

function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fromLocalInput(v: string): string | null {
  if (!v) return null
  const d = new Date(v)
  return isNaN(d.getTime()) ? null : d.toISOString()
}

export function QuickAddPreviewDialog({ open, draft, projects, onCancel, onSave }: Props) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<Priority>('medium')
  const [deadline, setDeadline] = useState('')
  const [tagInput, setTagInput] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Populate the form when a new draft arrives — done during render
  // rather than in an effect to avoid a cascading re-render.
  const [prevDraft, setPrevDraft] = useState(draft)
  if (draft !== prevDraft) {
    setPrevDraft(draft)
    if (draft) {
      setTitle(draft.title)
      setDescription(draft.description)
      setPriority(draft.priority)
      setDeadline(toLocalInput(draft.deadline))
      setTags(draft.tags)
      setProjectId(draft.project_id)
      setTagInput('')
    }
  }

  if (!draft) return null

  const confidencePct = Math.round(draft.confidence * 100)

  const addTag = () => {
    const t = tagInput.trim().toLowerCase()
    if (!t) return
    if (!tags.includes(t)) setTags([...tags, t])
    setTagInput('')
  }

  const removeTag = (t: string) => setTags(tags.filter((x) => x !== t))

  const save = async () => {
    if (!title.trim()) return
    setSaving(true)
    try {
      await onSave({
        title: title.trim(),
        description: description.trim(),
        priority,
        deadline: fromLocalInput(deadline),
        tags,
        project_id: projectId,
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onCancel() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-brand" />
            Todo prüfen
          </DialogTitle>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            {draft.used_fallback ? (
              <>
                <AlertTriangle className="h-3 w-3 text-amber-500" />
                <span>AI-Assist war nicht erreichbar — bitte Details ergänzen</span>
              </>
            ) : (
              <>
                <span>KI-Konfidenz: {confidencePct}%</span>
                <span className="text-border">·</span>
                <span className="truncate italic">„{draft.prompt}"</span>
              </>
            )}
          </div>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Titel</label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Beschreibung</label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Optional"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Projekt</label>
            <select
              value={projectId ?? NO_PROJECT}
              onChange={(e) => setProjectId(e.target.value === NO_PROJECT ? null : e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm outline-none focus:border-brand"
            >
              <option value={NO_PROJECT}>Kein Projekt</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Priorität</label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value as Priority)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm outline-none focus:border-brand"
              >
                {PRIORITY_OPTS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Fällig</label>
              <Input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Tags</label>
            <div className="flex flex-wrap gap-1.5">
              {tags.map((t) => (
                <Badge
                  key={t}
                  variant="secondary"
                  className="cursor-pointer px-2 py-0.5 text-[11px] hover:line-through"
                  onClick={() => removeTag(t)}
                >
                  {t} ×
                </Badge>
              ))}
              <input
                type="text"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ',') {
                    e.preventDefault()
                    addTag()
                  } else if (e.key === 'Backspace' && !tagInput && tags.length > 0) {
                    setTags(tags.slice(0, -1))
                  }
                }}
                placeholder="+ Tag, Enter"
                className="min-w-[100px] flex-1 bg-transparent px-2 py-0.5 text-xs outline-none placeholder:text-muted-foreground/70"
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={saving}>Abbrechen</Button>
          <Button onClick={save} disabled={saving || !title.trim()}>
            {saving ? 'Speichere...' : 'Erstellen'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
