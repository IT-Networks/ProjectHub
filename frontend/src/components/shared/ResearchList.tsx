import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useIsOffline } from '@/hooks/useOffline'
import { ErrorBanner } from './ErrorBanner'

interface ResearchItem {
  id: string
  query: string
  result: string
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

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.get<ResearchItem[]>(`/chat/research/${projectId}`)
      setItems(data)
    } catch {
      setItems([])
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [projectId])

  const handleResearch = async () => {
    if (!query.trim()) return
    setResearching(true)
    setError('')
    try {
      await api.post(`/chat/research/${projectId}`, { query: query.trim() })
      setQuery('')
      setDialogOpen(false)
      await load()
    } catch (e) {
      setError((e as Error).message || 'Recherche fehlgeschlagen')
    }
    setResearching(false)
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
                  onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
                >
                  {expandedId === item.id ? 'Einklappen' : 'Anzeigen'}
                </Button>
              </div>
            </div>
            {expandedId === item.id && (
              <div className="mt-3 rounded bg-muted/50 p-3">
                <div className="prose prose-sm prose-invert max-w-none whitespace-pre-wrap text-sm">
                  {item.result}
                </div>
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
    </div>
  )
}
