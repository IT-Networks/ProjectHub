import { useEffect, useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { RichTextEditor } from '@/components/shared/RichTextEditor'
import { CATEGORY_LABELS, CONFIDENCE_LABELS } from '@/lib/types'
import type { KnowledgeCategory, Confidence, KnowledgeItemCreate, KnowledgeItemUpdate } from '@/lib/types'

interface KnowledgeItemDialogProps {
  projectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  editItemId?: string | null
}

export function KnowledgeItemDialog({ projectId, open, onOpenChange, editItemId }: KnowledgeItemDialogProps) {
  const items = useKnowledgeStore((s) => s.items)
  const createItem = useKnowledgeStore((s) => s.createItem)
  const updateItem = useKnowledgeStore((s) => s.updateItem)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const fetchStats = useKnowledgeStore((s) => s.fetchStats)

  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [category, setCategory] = useState<KnowledgeCategory>('reference')
  const [confidence, setConfidence] = useState<Confidence>('medium')
  const [tagInput, setTagInput] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  const isEdit = !!editItemId
  const editItem = isEdit ? items.find((i) => i.id === editItemId) : null

  useEffect(() => {
    if (open && editItem) {
      setTitle(editItem.title)
      setContent(editItem.content)
      setCategory(editItem.category)
      setConfidence(editItem.confidence)
      setTags(editItem.tags)
    } else if (open && !editItem) {
      setTitle('')
      setContent('')
      setCategory('reference')
      setConfidence('medium')
      setTags([])
      setTagInput('')
    }
  }, [open, editItem])

  const handleAddTag = () => {
    const tag = tagInput.trim().toLowerCase()
    if (tag && !tags.includes(tag)) {
      setTags([...tags, tag])
    }
    setTagInput('')
  }

  const handleRemoveTag = (tag: string) => {
    setTags(tags.filter((t) => t !== tag))
  }

  const handleTagKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      handleAddTag()
    }
  }

  const handleSave = async () => {
    if (!title.trim()) return
    setSaving(true)
    try {
      if (isEdit && editItemId) {
        const data: KnowledgeItemUpdate = {
          title,
          content,
          category,
          confidence,
          tags,
        }
        await updateItem(projectId, editItemId, data)
      } else {
        const data: KnowledgeItemCreate = {
          title,
          content,
          category,
          confidence,
          tags,
        }
        await createItem(projectId, data)
      }
      onOpenChange(false)
      // Refresh all views
      fetchItems(projectId)
      fetchGraph(projectId)
      fetchStats(projectId)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Wissenseintrag bearbeiten' : 'Neuer Wissenseintrag'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Title */}
          <div>
            <label className="mb-1 block text-sm font-medium">Titel</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="z.B. REST API Design-Prinzipien"
              autoFocus
            />
          </div>

          {/* Content */}
          <div>
            <label className="mb-1 block text-sm font-medium">Inhalt</label>
            <RichTextEditor content={content} onChange={setContent} placeholder="Wissen beschreiben..." />
          </div>

          {/* Category + Confidence */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Kategorie</label>
              <Select value={category} onValueChange={(v) => setCategory(v as KnowledgeCategory)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
                    <SelectItem key={key} value={key}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Konfidenz</label>
              <Select value={confidence} onValueChange={(v) => setConfidence(v as Confidence)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(CONFIDENCE_LABELS).map(([key, label]) => (
                    <SelectItem key={key} value={key}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Tags */}
          <div>
            <label className="mb-1 block text-sm font-medium">Tags</label>
            <div className="flex gap-2">
              <Input
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={handleTagKeyDown}
                onBlur={handleAddTag}
                placeholder="Tag eingeben, Enter drücken"
                className="flex-1"
              />
            </div>
            {tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {tags.map((tag) => (
                  <Badge
                    key={tag}
                    variant="secondary"
                    className="cursor-pointer text-xs"
                    onClick={() => handleRemoveTag(tag)}
                  >
                    {tag} ✕
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Abbrechen
          </Button>
          <Button onClick={handleSave} disabled={!title.trim() || saving}>
            {saving ? 'Speichern...' : isEdit ? 'Aktualisieren' : 'Erstellen'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
