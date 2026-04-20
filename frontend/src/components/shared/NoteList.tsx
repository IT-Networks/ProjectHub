import { useEffect, useState, useMemo } from 'react'
import { useNoteStore } from '@/stores/noteStore'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { useToast } from '@/components/shared/Toast'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { RichTextEditor } from './RichTextEditor'
import { EmptyState } from '@/components/shared/EmptyState'
import { FormField } from '@/components/shared/FormField'
import { cn } from '@/lib/utils'
import { Plus, ChevronDown } from 'lucide-react'
import type { Note } from '@/lib/types'

interface Props {
  projectId: string
}

export function NoteList({ projectId }: Props) {
  const notes = useNoteStore((s) => s.notes)
  const fetchNotes = useNoteStore((s) => s.fetchNotes)
  const createNote = useNoteStore((s) => s.createNote)
  const updateNote = useNoteStore((s) => s.updateNote)
  const deleteNote = useNoteStore((s) => s.deleteNote)
  const togglePin = useNoteStore((s) => s.togglePin)
  const addLinkedKnowledge = useNoteStore((s) => s.addLinkedKnowledge)
  const importNote = useKnowledgeStore((s) => s.importNote)
  const { success, error } = useToast()

  const [importedIds, setImportedIds] = useState<Set<string>>(new Set())
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set())
  const [editOpen, setEditOpen] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [deletedNoteBackup, setDeletedNoteBackup] = useState<{ id: string; note: Note } | null>(null)
  const [validFields, setValidFields] = useState<Record<string, boolean>>({})
  const [form, setForm] = useState({ title: '', content: '', deadline: '' })

  const toggleExpanded = (noteId: string) => {
    setExpandedNotes((prev) => {
      const next = new Set(prev)
      if (next.has(noteId)) {
        next.delete(noteId)
      } else {
        next.add(noteId)
      }
      return next
    })
  }

  const projectNotes = useMemo(() => notes.filter((n) => n.project_id === projectId), [notes, projectId])

  useEffect(() => {
    fetchNotes(projectId)
  }, [fetchNotes, projectId])

  const openNew = () => {
    setEditId(null)
    setForm({ title: '', content: '', deadline: '' })
    setEditOpen(true)
  }

  const openEdit = (id: string) => {
    const note = projectNotes.find((n) => n.id === id)
    if (!note) return
    setEditId(id)
    setForm({ title: note.title, content: note.content, deadline: note.deadline || '' })
    setEditOpen(true)
  }

  const handleSave = async () => {
    if (!form.title.trim()) {
      error('Titel erforderlich')
      return
    }

    setIsSaving(true)
    try {
      if (editId) {
        await updateNote(editId, { title: form.title, content: form.content, deadline: form.deadline || null })
        success('Notiz aktualisiert!')
      } else {
        await createNote({ project_id: projectId, title: form.title, content: form.content, deadline: form.deadline || null })
        success('Notiz erstellt!')
      }
      setEditOpen(false)
    } catch (err) {
      error(`Fehler: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
    } finally {
      setIsSaving(false)
    }
  }

  const handleDelete = async (noteId: string) => {
    const noteToDelete = projectNotes.find((n) => n.id === noteId)
    if (!noteToDelete) return

    // Optimistic delete
    setDeletedNoteBackup({ id: noteId, note: noteToDelete })

    // Show undo toast
    success('Notiz gelöscht', {
      action: {
        label: 'Rückgängig',
        onClick: () => {
          setDeletedNoteBackup(null)
        },
      },
      duration: 5000,
    })

    // Actually delete after 5 seconds
    setTimeout(async () => {
      try {
        await deleteNote(noteId)
        setDeletedNoteBackup(null)
      } catch (err) {
        error('Fehler beim Löschen')
        setDeletedNoteBackup(null)
      }
    }, 5000)
  }

  const handleSyncLinkedKnowledge = async (noteId: string) => {
    const note = projectNotes.find((n) => n.id === noteId)
    if (!note || note.linked_knowledge_ids.length === 0) return

    try {
      const knowledgeStore = useKnowledgeStore.getState()
      await knowledgeStore.syncNoteToKnowledge(projectId, noteId, note.content, note.title)
      success('Wissen synchronisiert!')
    } catch (err) {
      error('Fehler beim Synchronisieren')
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{projectNotes.length} Notizen</span>
        <Button size="sm" onClick={openNew}>+ Notiz</Button>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {projectNotes.filter((n) => !deletedNoteBackup || n.id !== deletedNoteBackup.id).map((note) => {
          const isOverdue = note.deadline && new Date(note.deadline) < new Date()
          return (
            <Card key={note.id} className="group relative p-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 flex-1">
                  <button
                    onClick={() => toggleExpanded(note.id)}
                    className="p-0.5 hover:bg-accent rounded transition-colors flex-shrink-0"
                  >
                    <ChevronDown
                      className={cn(
                        'w-4 h-4 transition-transform',
                        expandedNotes.has(note.id) ? 'rotate-180' : ''
                      )}
                    />
                  </button>
                  <h4 className="text-sm font-medium flex-1">{note.title || 'Ohne Titel'}</h4>
                </div>
                <div className="flex items-center gap-1">
                  {note.is_pinned && <Badge variant="secondary" className="text-xs">Gepinnt</Badge>}
                  {note.deadline && (
                    <span className={cn('text-xs', isOverdue ? 'text-red-400' : 'text-muted-foreground')}>
                      {new Date(note.deadline).toLocaleDateString('de-DE')}
                    </span>
                  )}
                </div>
              </div>
              <div
                className={cn(
                  'tiptap text-sm text-muted-foreground space-y-1 transition-all overflow-hidden',
                  expandedNotes.has(note.id)
                    ? 'max-h-[500px] overflow-y-auto'
                    : 'line-clamp-3 max-h-[80px]'
                )}
              >
                {note.content ? (
                  <div dangerouslySetInnerHTML={{ __html: note.content }} />
                ) : (
                  <em>Leer</em>
                )}
              </div>
              <div className="mt-3 flex flex-wrap gap-2 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
                <Button variant="ghost" size="sm" onClick={() => openEdit(note.id)}>Bearbeiten</Button>
                <Button variant="ghost" size="sm" onClick={() => togglePin(note.id)}>
                  {note.is_pinned ? 'Entpinnen' : 'Pinnen'}
                </Button>
                {(note.linked_knowledge_ids?.length ?? 0) > 0 && (
                  <Button variant="ghost" size="sm" className="text-blue-400" onClick={() => handleSyncLinkedKnowledge(note.id)}>
                    🔄 Sync ({note.linked_knowledge_ids?.length ?? 0})
                  </Button>
                )}
                {(note.linked_knowledge_ids?.length ?? 0) > 0 ? (
                  <span className="px-2 py-1 text-xs text-green-500">✓ Zu Wissen verknüpft</span>
                ) : (
                  <Button variant="ghost" size="sm" onClick={async () => {
                    try {
                      const knowledge = await importNote(projectId, note.id)
                      await addLinkedKnowledge(note.id, knowledge.id)
                      setImportedIds((prev) => new Set([...prev, note.id]))
                      success('In Wissensdatenbank importiert!')
                    } catch (err) {
                      error('Fehler beim Importieren')
                    }
                  }}>
                    → Wissen
                  </Button>
                )}
                <Button variant="ghost" size="sm" className="text-red-400" onClick={() => handleDelete(note.id)}>
                  Löschen
                </Button>
              </div>
            </Card>
          )
        })}
        {projectNotes.length === 0 && (
          <EmptyState
            icon="📝"
            title="Keine Notizen vorhanden"
            description="Erstelle deine erste Notiz um Gedanken, Ideen und Beobachtungen festzuhalten."
            action={<Button onClick={openNew} icon={<Plus className="w-4 h-4" />}>Erste Notiz erstellen</Button>}
          />
        )}
      </div>

      {/* Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editId ? 'Notiz bearbeiten' : 'Neue Notiz'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="flex gap-4">
              <div className="flex-1">
                <FormField
                  label="Titel"
                  error={!form.title.trim() && form.title !== '' ? 'Titel erforderlich' : undefined}
                  success={validFields.title && form.title.trim().length > 0}
                >
                  <Input
                    value={form.title}
                    onChange={(e) => {
                      setForm({ ...form, title: e.target.value })
                      setValidFields({ ...validFields, title: e.target.value.trim().length > 0 })
                    }}
                    onBlur={() => setValidFields({ ...validFields, title: form.title.trim().length > 0 })}
                    placeholder="Titel..."
                    autoFocus
                    aria-invalid={!form.title.trim() && form.title !== ''}
                  />
                </FormField>
              </div>
              <div className="w-40">
                <label className="mb-1 block text-sm font-medium">Frist</label>
                <Input type="date" value={form.deadline} onChange={(e) => setForm({ ...form, deadline: e.target.value })} />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Inhalt</label>
              <RichTextEditor content={form.content} onChange={(c) => setForm({ ...form, content: c })} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)} disabled={isSaving}>Abbrechen</Button>
            <Button onClick={handleSave} disabled={!form.title.trim() || isSaving}>
              {isSaving ? 'Speichert...' : 'Speichern'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
