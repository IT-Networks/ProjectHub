import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useIsOffline } from '@/hooks/useOffline'
import { ErrorBanner } from './ErrorBanner'
import { ConfirmDialog } from './ConfirmDialog'
import { Markdown } from './Markdown'

// Listen-Payload: Metadaten OHNE den vollen result-Text. Der Volltext wird
// lazy via GET /chat/research/{pid}/{id} nachgeladen, sobald ein Eintrag
// aufgeklappt wird (spart bei jedem Tab-Aufruf das Übertragen aller
// LLM-Ergebnistexte).
interface ResearchItem {
  id: string
  query: string
  model_used: string
  agent_team: string
  created_at: string
}

interface Props {
  projectId: string
}

export function ResearchList({ projectId }: Props) {
  const isOffline = useIsOffline()
  const importResearch = useKnowledgeStore((s) => s.importResearch)
  const [items, setItems] = useState<ResearchItem[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [researching, setResearching] = useState(false)
  const [error, setError] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [importedIds, setImportedIds] = useState<Set<string>>(new Set())
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [resultCache, setResultCache] = useState<Record<string, string>>({})
  const [loadingResult, setLoadingResult] = useState<string | null>(null)

  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await api.get<ResearchItem[]>(`/chat/research/${projectId}`)
        if (cancelled) return
        setItems(data)
        try {
          const imported = await api.get<string[]>(`/knowledge/${projectId}/imports/research`)
          if (!cancelled) setImportedIds(new Set(imported))
        } catch {
          // ignore — endpoint may be unavailable in older backend
        }
      } catch {
        if (!cancelled) setItems([])
      }
      if (!cancelled) setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [projectId, refreshKey])

  const handleResearch = async () => {
    if (!query.trim()) return
    setResearching(true)
    setError('')
    try {
      await api.post(`/chat/research/${projectId}`, { query: query.trim() })
      setQuery('')
      setDialogOpen(false)
      setRefreshKey((k) => k + 1)
    } catch (e) {
      setError((e as Error).message || 'Recherche fehlgeschlagen')
    }
    setResearching(false)
  }

  const handleDelete = async (id: string) => {
    try {
      await api.del(`/chat/research/${projectId}/${id}`)
      setItems((prev) => prev.filter((it) => it.id !== id))
      setResultCache((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
      if (expandedId === id) setExpandedId(null)
    } catch (e) {
      setError((e as Error).message || 'Löschen fehlgeschlagen')
    }
  }

  const handleToggleExpand = async (id: string) => {
    if (expandedId === id) {
      setExpandedId(null)
      return
    }
    setExpandedId(id)
    // Volltext nur einmal pro Eintrag laden — danach aus dem Cache.
    if (resultCache[id] === undefined) {
      setLoadingResult(id)
      try {
        const detail = await api.get<{ result: string }>(`/chat/research/${projectId}/${id}`)
        setResultCache((prev) => ({ ...prev, [id]: detail.result ?? '' }))
      } catch (e) {
        setError((e as Error).message || 'Ergebnis konnte nicht geladen werden')
        setExpandedId((cur) => (cur === id ? null : cur))
      } finally {
        setLoadingResult(null)
      }
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{items.length} Recherchen</span>
        <Button size="sm" onClick={() => setDialogOpen(true)} disabled={isOffline}>
          + Neue Recherche
        </Button>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError('')} />}

      <div className="space-y-3">
        {items.map((item) => (
          <Card key={item.id} className="p-4">
            <div className="flex items-start justify-between">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{item.query}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {new Date(item.created_at).toLocaleString('de-DE')}
                  {item.agent_team && ` — Team: ${item.agent_team}`}
                </p>
              </div>
              <div className="flex gap-1">
                {importedIds.has(item.id) ? (
                  <span className="px-2 py-1 text-xs text-green-500">✓ Importiert</span>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      await importResearch(projectId, item.id)
                      setImportedIds((prev) => new Set([...prev, item.id]))
                    }}
                    title="Als Wissenseintrag speichern"
                  >
                    → Wissen
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleToggleExpand(item.id)}
                >
                  {expandedId === item.id ? 'Einklappen' : 'Anzeigen'}
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setDeletingId(item.id)}
                  title="Recherche löschen"
                >
                  <Trash2 className="h-4 w-4 text-muted-foreground" />
                </Button>
              </div>
            </div>
            {expandedId === item.id && (
              <div className="mt-3 rounded bg-muted/50 p-3">
                {loadingResult === item.id ? (
                  <p className="text-sm text-muted-foreground">Lädt Ergebnis…</p>
                ) : (
                  <Markdown>{resultCache[item.id] ?? ''}</Markdown>
                )}
              </div>
            )}
          </Card>
        ))}

        {!loading && items.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">
            Noch keine Recherchen. Starte eine LLM-gestützte Analyse im Projektkontext.
          </p>
        )}
      </div>

      {/* Research Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Neue Recherche</DialogTitle>
          </DialogHeader>
          <div className="py-2">
            <label className="mb-1 block text-sm font-medium">Frage / Analyseauftrag</label>
            <Textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="z.B. Analysiere die letzten fehlgeschlagenen Builds und finde das Muster..."
              rows={4}
              autoFocus
            />
            <p className="mt-2 text-xs text-muted-foreground">
              Der Projektkontext (Todos, Notizen, Builds, PRs) wird automatisch mitgesendet.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Abbrechen</Button>
            <Button onClick={handleResearch} disabled={!query.trim() || researching}>
              {researching ? 'Recherchiert...' : 'Recherche starten'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!deletingId}
        onOpenChange={() => setDeletingId(null)}
        title="Recherche löschen"
        description="Diese Recherche wird dauerhaft gelöscht. Bereits nach Wissen importierte Einträge bleiben erhalten."
        confirmLabel="Löschen"
        onConfirm={() => {
          if (deletingId) handleDelete(deletingId)
          setDeletingId(null)
        }}
      />
    </div>
  )
}
