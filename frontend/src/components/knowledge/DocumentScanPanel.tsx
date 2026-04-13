import { useEffect, useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { useSSEEvent } from '@/hooks/useSSE'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'

interface DocumentScanPanelProps {
  projectId: string
  docsPath: string | null
}

interface ScanProgressEvent {
  project_id: string
  document_id?: string
  file_name?: string
  phase?: string
  current?: number
  total?: number
  total_chunks?: number
  current_section?: string
  items_created?: number
  message?: string
}

interface ScanCompleteEvent {
  project_id: string
  scanned: number
  total_items: number
  total_docs: number
}

const STATUS_ICONS: Record<string, string> = {
  pending: '⏳',
  scanning: '⟳',
  done: '✓',
  error: '✗',
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Nicht gescannt',
  scanning: 'Wird gescannt...',
  done: 'Gescannt',
  error: 'Fehler',
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function DocumentScanPanel({ projectId, docsPath }: DocumentScanPanelProps) {
  const documents = useKnowledgeStore((s) => s.documents)
  const fetchDocuments = useKnowledgeStore((s) => s.fetchDocuments)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const fetchStats = useKnowledgeStore((s) => s.fetchStats)

  const [scanning, setScanning] = useState(false)
  const [progress, setProgress] = useState<ScanProgressEvent | null>(null)
  const [lastResult, setLastResult] = useState<ScanCompleteEvent | null>(null)
  const [deleteDocId, setDeleteDocId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (docsPath) {
      fetchDocuments(projectId)
    }
  }, [projectId, docsPath, fetchDocuments])

  // Listen for scan progress events
  useSSEEvent('doc_scan_progress', (data: ScanProgressEvent) => {
    if (data.project_id === projectId) {
      setProgress(data)
      if (data.phase === 'done') {
        fetchDocuments(projectId)
      }
    }
  })

  useSSEEvent('doc_scan_complete', (data: ScanCompleteEvent) => {
    if (data.project_id === projectId) {
      setScanning(false)
      setProgress(null)
      setLastResult(data)
      fetchDocuments(projectId)
      fetchItems(projectId)
      fetchGraph(projectId)
      fetchStats(projectId)
    }
  })

  const handleScanAll = async () => {
    setScanning(true)
    setLastResult(null)
    try {
      await api.post(`/knowledge/${projectId}/scan-docs`, { force: false })
    } catch (e) {
      setScanning(false)
    }
  }

  const handleRescanAll = async () => {
    setScanning(true)
    setLastResult(null)
    try {
      await api.post(`/knowledge/${projectId}/scan-docs`, { force: true })
    } catch (e) {
      setScanning(false)
    }
  }

  const handleRescanDoc = async (docId: string) => {
    setScanning(true)
    try {
      await api.post(`/knowledge/${projectId}/scan-doc/${docId}`)
    } catch (e) {
      setScanning(false)
    }
  }

  const handleDeleteDoc = async () => {
    if (!deleteDocId) return
    await api.del(`/knowledge/${projectId}/documents/${deleteDocId}`)
    setDeleteDocId(null)
    fetchDocuments(projectId)
    fetchItems(projectId)
    fetchGraph(projectId)
    fetchStats(projectId)
  }

  if (!docsPath) return null

  return (
    <div className="mb-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/50"
      >
        <span className="text-xs">{expanded ? '▼' : '▶'}</span>
        <span className="font-medium">Projektdokumente</span>
        <span className="text-xs text-muted-foreground">({docsPath})</span>
        {documents.length > 0 && (
          <Badge variant="secondary" className="ml-auto text-xs">
            {documents.length} Dateien
          </Badge>
        )}
      </button>

      {expanded && (
        <div className="mt-2 rounded-md border border-border p-3 space-y-3">
          {/* Actions */}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={handleScanAll} disabled={scanning}>
              {scanning ? 'Wird gescannt...' : 'Neue scannen'}
            </Button>
            <Button size="sm" variant="outline" onClick={handleRescanAll} disabled={scanning}>
              Alle neu scannen
            </Button>
          </div>

          {/* Progress */}
          {scanning && progress && (
            <div className="rounded bg-muted/50 p-2 text-xs space-y-1">
              <div className="flex items-center gap-2">
                <span className="animate-spin">⟳</span>
                <span className="font-medium">{progress.file_name}</span>
                <span className="text-muted-foreground">— {progress.phase}</span>
              </div>
              {progress.current_section && (
                <p className="text-muted-foreground pl-5">Abschnitt: {progress.current_section}</p>
              )}
              {progress.current && progress.total_chunks && (
                <div className="pl-5">
                  <div className="h-1.5 w-full rounded-full bg-muted">
                    <div
                      className="h-1.5 rounded-full bg-primary transition-all"
                      style={{ width: `${(progress.current / progress.total_chunks) * 100}%` }}
                    />
                  </div>
                  <span className="text-muted-foreground">
                    Chunk {progress.current}/{progress.total_chunks}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Last Result */}
          {lastResult && (
            <div className="rounded bg-green-500/10 border border-green-500/20 p-2 text-xs text-green-400">
              Scan abgeschlossen: {lastResult.scanned} Dokumente gescannt, {lastResult.total_items} Wissenseinträge erstellt
            </div>
          )}

          {/* Document List */}
          {documents.length > 0 ? (
            <div className="space-y-1">
              {documents.map((doc) => (
                <Card key={doc.id} className="flex items-center justify-between p-2 text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    <span>📄</span>
                    <span className="truncate font-medium">{doc.file_name}</span>
                    <span className="text-muted-foreground">{formatFileSize(doc.file_size)}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge
                      variant={doc.scan_status === 'done' ? 'secondary' : 'outline'}
                      className="text-[10px]"
                    >
                      {STATUS_ICONS[doc.scan_status]} {doc.extracted_items} Items
                    </Badge>
                    {doc.last_scanned_at && (
                      <span className="text-muted-foreground text-[10px]">
                        {new Date(doc.last_scanned_at).toLocaleDateString('de-DE')}
                      </span>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-1 text-[10px]"
                      onClick={() => handleRescanDoc(doc.id)}
                      disabled={scanning}
                    >
                      ↻
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-1 text-[10px] text-destructive"
                      onClick={() => setDeleteDocId(doc.id)}
                    >
                      ✕
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Keine Dokumente gescannt. Klicke "Neue scannen" um Spezifikationen zu analysieren.
            </p>
          )}
        </div>
      )}

      <ConfirmDialog
        open={!!deleteDocId}
        onOpenChange={() => setDeleteDocId(null)}
        title="Dokument entfernen"
        description="Das Dokument und alle daraus extrahierten Wissenseinträge werden gelöscht."
        confirmLabel="Entfernen"
        onConfirm={handleDeleteDoc}
      />
    </div>
  )
}
