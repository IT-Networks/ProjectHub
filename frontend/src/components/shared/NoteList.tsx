import { useEffect, useState, useMemo } from 'react'
import { useNoteStore } from '@/stores/noteStore'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { RichTextEditor } from './RichTextEditor'
import { ConfirmDialog } from './ConfirmDialog'
import { cn } from '@/lib/utils'

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
  const importNote = useKnowledgeStore((s) => s.importNote)
  const [importedIds, setImportedIds] = useState<Set<string>>(new Set())
  const [editOpen, setEditOpen] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [form, setForm] = useState({ title: '', content: '', deadline: '' })

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
    if (editId) {
      await updateNote(editId, { title: form.title, content: form.content, deadline: form.deadline || null })
    } else {
      await createNote({ project_id: projectId, title: form.title, content: form.content, deadline: form.deadline || null })
    }
    setEditOpen(false)
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{projectNotes.length} Notizen</span>
        <Button size="sm" onClick={openNew}>+ Notiz</Button>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {projectNotes.map((note) => {
          const isOverdue = note.deadline && new Date(note.deadline) < new Date()
          return (
            <Card key={note.id} className="group relative p-4">
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-sm font-medium">{note.title || 'Ohne Titel'}</h4>
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
                className="prose prose-sm prose-invert max-w-none line-clamp-4 text-sm text-muted-foreground"
                dangerouslySetInnerHTML={{ __html: note.content || '<em>Leer</em>' }}
              />
              <div className="mt-3 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
                <Button variant="ghost" size="sm" onClick={() => openEdit(note.id)}>Bearbeiten</Button>
                <Button variant="ghost" size="sm" onClick={() => togglePin(note.id)}>
                  {note.is_pinned ? 'Entpinnen' : 'Pinnen'}
                </Button>
                {importedIds.has(note.id) ? (
                  <span className="px-2 py-1 text-xs text-green-500">✓ Importiert</span>
                ) : (
                  <Button variant="ghost" size="sm" onClick={async () => {
                    await importNote(projectId, note.id)
                    setImportedIds((prev) => new Set([...prev, note.id]))
                  }}>
                    → Wissen
                  </Button>
                )}
                <Button variant="ghost" size="sm" className="text-red-400" onClick={() => setDeletingId(note.id)}>
                  Löschen
                </Button>
              </div>
            </Card>
          )
        })}
        {projectNotes.length === 0 && (
          <p className="col-span-full py-8 text-center text-sm text-muted-foreground">Noch keine Notizen</p>
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
                <label className="mb-1 block text-sm font-medium">Titel</label>
                <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Titel..." autoFocus />
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
            <Button variant="outline" onClick={() => setEditOpen(false)}>Abbrechen</Button>
            <Button onClick={handleSave}>Speichern</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deletingId}
        onOpenChange={() => setDeletingId(null)}
        title="Notiz löschen"
        description="Diese Notiz wird unwiderruflich gelöscht."
        confirmLabel="Löschen"
        onConfirm={() => {
          if (deletingId) deleteNote(deletingId)
          setDeletingId(null)
        }}
      />
    </div>
  )
}
