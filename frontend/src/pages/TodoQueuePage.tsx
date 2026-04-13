import { useEffect, useState } from 'react'
import { useTodoQueueStore } from '@/stores/todoQueueStore'
import { useProjectStore } from '@/stores/projectStore'
import { useSSEEvent } from '@/hooks/useSSE'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PRIORITY_LABELS } from '@/lib/types'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { cn } from '@/lib/utils'

export function TodoQueuePage() {
  const items = useTodoQueueStore((s) => s.items)
  const stats = useTodoQueueStore((s) => s.stats)
  const loading = useTodoQueueStore((s) => s.loading)
  const fetchQueue = useTodoQueueStore((s) => s.fetchQueue)
  const fetchStats = useTodoQueueStore((s) => s.fetchStats)
  const acceptItem = useTodoQueueStore((s) => s.acceptItem)
  const rejectItem = useTodoQueueStore((s) => s.rejectItem)
  const updateItem = useTodoQueueStore((s) => s.updateItem)
  const projects = useProjectStore((s) => s.projects)
  const fetchProjects = useProjectStore((s) => s.fetchProjects)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [rejectingId, setRejectingId] = useState<string | null>(null)

  useEffect(() => {
    fetchQueue('pending')
    fetchStats()
    fetchProjects()
  }, [fetchQueue, fetchStats, fetchProjects])

  // Live updates
  useSSEEvent('queue_item', () => {
    fetchQueue('pending')
    fetchStats()
  })

  const pendingItems = items.filter((i) => i.queue_status === 'pending')

  const handleAccept = async (id: string, projectId?: string | null) => {
    await acceptItem(id, projectId)
  }

  const startEdit = (id: string, title: string) => {
    setEditingId(id)
    setEditTitle(title)
  }

  const saveEdit = async (id: string) => {
    if (editTitle.trim()) {
      await updateItem(id, { suggested_title: editTitle })
    }
    setEditingId(null)
  }

  return (
    <div className="p-6">
      {/* Stats */}
      <div className="mb-6 flex items-center gap-4">
        <Badge variant="default" className="text-sm">
          {stats.pending} ausstehend
        </Badge>
        <Badge variant="secondary" className="text-sm">
          {stats.accepted} angenommen
        </Badge>
        <Badge variant="outline" className="text-sm">
          {stats.rejected} abgelehnt
        </Badge>
      </div>

      {loading && <p className="text-sm text-muted-foreground">Laden...</p>}

      <div className="space-y-3">
        {pendingItems.map((item) => (
          <Card key={item.id} className="p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                {/* Title (editable) */}
                {editingId === item.id ? (
                  <div className="flex gap-2">
                    <Input
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="flex-1"
                      autoFocus
                      onKeyDown={(e) => e.key === 'Enter' && saveEdit(item.id)}
                    />
                    <Button size="sm" onClick={() => saveEdit(item.id)}>OK</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>X</Button>
                  </div>
                ) : (
                  <h3
                    className="cursor-pointer text-sm font-medium hover:text-primary"
                    onClick={() => startEdit(item.id, item.suggested_title)}
                  >
                    {item.suggested_title}
                  </h3>
                )}

                {item.suggested_description && (
                  <p className="mt-1 text-xs text-muted-foreground">{item.suggested_description}</p>
                )}

                {/* Source info */}
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline" className="text-xs">
                    {item.source === 'email' ? 'Email' : 'Webex'}
                  </Badge>
                  <span>{item.source_sender}</span>
                  <span>—</span>
                  <span>{item.source_subject}</span>
                  {item.source_date && (
                    <span>{new Date(item.source_date).toLocaleDateString('de-DE')}</span>
                  )}
                </div>

                {/* AI Analysis */}
                {item.ai_analysis && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                      AI-Analyse ({Math.round(item.ai_confidence * 100)}% Konfidenz)
                    </summary>
                    <p className="mt-1 rounded bg-muted/50 p-2 text-xs">{item.ai_analysis}</p>
                  </details>
                )}
              </div>

              {/* Actions */}
              <div className="flex flex-col items-end gap-2">
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className={cn('text-xs', {
                    'bg-red-500/20 text-red-400': item.suggested_priority === 'high',
                    'bg-yellow-500/20 text-yellow-400': item.suggested_priority === 'medium',
                    'bg-blue-500/20 text-blue-400': item.suggested_priority === 'low',
                  })}>
                    {PRIORITY_LABELS[item.suggested_priority]}
                  </Badge>
                  {item.suggested_deadline && (
                    <span className="text-xs text-muted-foreground">
                      Frist: {new Date(item.suggested_deadline).toLocaleDateString('de-DE')}
                    </span>
                  )}
                </div>

                {/* Project picker + accept */}
                <div className="flex items-center gap-2">
                  <Select
                    value={item.suggested_project_id || '__none__'}
                    onValueChange={(v) => {
                      const pid = v === '__none__' ? null : v
                      updateItem(item.id, { suggested_project_id: pid })
                    }}
                  >
                    <SelectTrigger className="h-8 w-[160px] text-xs">
                      <SelectValue placeholder="Projekt..." />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">Kein Projekt</SelectItem>
                      {projects.map((p) => (
                        <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  <Button
                    size="sm"
                    onClick={() => handleAccept(item.id, item.suggested_project_id)}
                    className="bg-green-600 hover:bg-green-700"
                  >
                    Annehmen
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setRejectingId(item.id)}
                  >
                    Ablehnen
                  </Button>
                </div>
              </div>
            </div>
          </Card>
        ))}

        {!loading && pendingItems.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">
            Keine ausstehenden Vorschläge. Neue Items erscheinen automatisch aus Email/Chat-Analyse.
          </p>
        )}
      </div>

      <ConfirmDialog
        open={!!rejectingId}
        onOpenChange={() => setRejectingId(null)}
        title="Vorschlag ablehnen"
        description="Dieser Vorschlag wird als abgelehnt markiert und verschwindet aus der Queue."
        confirmLabel="Ablehnen"
        onConfirm={() => {
          if (rejectingId) rejectItem(rejectingId)
          setRejectingId(null)
        }}
      />
    </div>
  )
}
