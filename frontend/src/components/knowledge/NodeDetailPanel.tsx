import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  CATEGORY_LABELS,
  CATEGORY_COLORS,
  SOURCE_TYPE_KB_LABELS,
  CONFIDENCE_LABELS,
  EDGE_TYPE_LABELS,
} from '@/lib/types'
import type { KnowledgeCategory, KnowledgeSourceType, Confidence, EdgeType } from '@/lib/types'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { SuggestedLinksPanel } from './SuggestedLinksPanel'
import { useState } from 'react'

interface NodeDetailPanelProps {
  projectId: string
  onEdit: (itemId: string) => void
  onClose: () => void
}

export function NodeDetailPanel({ projectId, onEdit, onClose }: NodeDetailPanelProps) {
  const selectedItemDetail = useKnowledgeStore((s) => s.selectedItemDetail)
  const selectedItemId = useKnowledgeStore((s) => s.selectedItemId)
  const deleteItem = useKnowledgeStore((s) => s.deleteItem)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchStats = useKnowledgeStore((s) => s.fetchStats)
  const setSelectedItem = useKnowledgeStore((s) => s.setSelectedItem)
  const fetchItemDetail = useKnowledgeStore((s) => s.fetchItemDetail)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)

  if (!selectedItemDetail || !selectedItemId) return null

  const item = selectedItemDetail

  const handleDelete = async () => {
    await deleteItem(projectId, item.id)
    setSelectedItem(null)
    fetchGraph(projectId)
    fetchStats(projectId)
  }

  const handleNeighborClick = (id: string) => {
    setSelectedItem(id)
    fetchItemDetail(projectId, id)
  }

  const handleSyncKnowledgeToNote = async () => {
    try {
      const syncKnowledgeToNote = useKnowledgeStore.getState().syncKnowledgeToNote
      await syncKnowledgeToNote(projectId, item.id)
    } catch (err) {
      console.error('Sync failed:', err)
    }
  }

  return (
    <div className="w-80 shrink-0 overflow-y-auto rounded-lg border border-border bg-card p-4">
      {/* Header */}
      <div className="mb-3 flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span
            className="h-3 w-3 rounded-full"
            style={{ backgroundColor: CATEGORY_COLORS[item.category as KnowledgeCategory] }}
          />
          <h3 className="text-sm font-semibold leading-tight">{item.title}</h3>
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-lg leading-none">
          ✕
        </button>
      </div>

      {/* Meta Badges */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        <Badge variant="outline" className="text-[10px]">
          {CATEGORY_LABELS[item.category as KnowledgeCategory]}
        </Badge>
        <Badge variant="secondary" className="text-[10px]">
          {SOURCE_TYPE_KB_LABELS[item.source_type as KnowledgeSourceType]}
        </Badge>
        <Badge variant="secondary" className="text-[10px]">
          {CONFIDENCE_LABELS[item.confidence as Confidence]}
        </Badge>
        {item.is_pinned && <Badge variant="secondary" className="text-[10px]">📌 Gepinnt</Badge>}
      </div>

      {/* Tags */}
      {item.tags.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {item.tags.map((tag) => (
            <Badge key={tag} variant="outline" className="text-[10px] px-1.5 py-0">
              {tag}
            </Badge>
          ))}
        </div>
      )}

      {/* Content */}
      {item.content && (
        <div className="tiptap mb-4 max-h-[400px] overflow-y-auto rounded bg-muted/30 p-3 text-xs text-muted-foreground">
          <div dangerouslySetInnerHTML={{ __html: item.content }} />
        </div>
      )}

      {/* Neighbors */}
      {item.neighbors.length > 0 && (
        <div className="mb-4">
          <h4 className="mb-2 text-xs font-medium text-muted-foreground uppercase">
            Verknüpft mit ({item.neighbors.length})
          </h4>
          <div className="space-y-1">
            {item.neighbors.map((nb) => (
              <Card
                key={nb.id}
                className="cursor-pointer p-2 text-xs transition-colors hover:bg-muted/50"
                onClick={() => handleNeighborClick(nb.id)}
              >
                <div className="flex items-center gap-2">
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: CATEGORY_COLORS[nb.category as KnowledgeCategory] }}
                  />
                  <span className="truncate">{nb.title}</span>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Edges */}
      {item.edges.length > 0 && (
        <div className="mb-4">
          <h4 className="mb-2 text-xs font-medium text-muted-foreground uppercase">
            Verbindungen ({item.edges.length})
          </h4>
          <div className="space-y-1 text-xs text-muted-foreground">
            {item.edges.map((e) => (
              <div key={e.id} className="flex items-center gap-1">
                <span>{EDGE_TYPE_LABELS[e.edge_type as EdgeType]}</span>
                {e.label && <span className="text-[10px]">({e.label})</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Timestamps */}
      <div className="mb-4 text-[10px] text-muted-foreground">
        <p>Erstellt: {new Date(item.created_at).toLocaleDateString('de-DE')}</p>
        <p>Aktualisiert: {new Date(item.updated_at).toLocaleDateString('de-DE')}</p>
      </div>

      {/* Suggested Links */}
      <div className="mb-4">
        <SuggestedLinksPanel projectId={projectId} itemId={item.id} />
      </div>

      {/* Sync Status */}
      {item.source_note_id ? (
        <div className="mb-4 flex items-center gap-2 rounded bg-blue-500/10 p-2">
          <span className="text-xs font-medium text-blue-400">
            {(item.sync_status ?? 'synced') === 'synced' ? '✓ Synchronisiert' : '⏳ Ausstehend'}
          </span>
          {item.last_synced_at && (
            <span className="text-[10px] text-muted-foreground">
              {new Date(item.last_synced_at).toLocaleDateString('de-DE')}
            </span>
          )}
        </div>
      ) : null}

      {/* Actions */}
      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="flex-1" onClick={() => onEdit(item.id)}>
            Bearbeiten
          </Button>
          <Button variant="destructive" size="sm" onClick={() => setDeleteConfirmOpen(true)}>
            Löschen
          </Button>
        </div>
        {item.source_note_id && (
          <Button variant="secondary" size="sm" className="w-full" onClick={handleSyncKnowledgeToNote}>
            ↑ Änderungen in Notiz übernehmen
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={deleteConfirmOpen}
        onOpenChange={setDeleteConfirmOpen}
        title="Wissenseintrag löschen"
        description={`"${item.title}" und alle Verknüpfungen werden unwiderruflich gelöscht.`}
        confirmLabel="Löschen"
        onConfirm={handleDelete}
      />
    </div>
  )
}
