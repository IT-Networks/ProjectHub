import { useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface ResearchDialogProps {
  projectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ResearchDialog({ projectId, open, onOpenChange }: ResearchDialogProps) {
  const researchTopic = useKnowledgeStore((s) => s.researchTopic)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const fetchStats = useKnowledgeStore((s) => s.fetchStats)

  const [topic, setTopic] = useState('')
  const [confluenceUrl, setConfluenceUrl] = useState('')
  const [confluenceSpace, setConfluenceSpace] = useState('')
  const [includeChildren, setIncludeChildren] = useState(false)
  const [researching, setResearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isConfluence = Boolean(confluenceUrl.trim() || confluenceSpace.trim())

  const reset = () => {
    setTopic('')
    setConfluenceUrl('')
    setConfluenceSpace('')
    setIncludeChildren(false)
  }

  const handleResearch = async () => {
    if (!topic.trim()) return
    setResearching(true)
    setError(null)
    try {
      await researchTopic(projectId, topic, {
        confluencePageUrl: confluenceUrl.trim() || undefined,
        confluenceSpace: confluenceSpace.trim() || undefined,
        includeChildren,
      })
      onOpenChange(false)
      reset()
      fetchItems(projectId)
      fetchGraph(projectId)
      fetchStats(projectId)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setResearching(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Thema recherchieren</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <p className="text-sm text-muted-foreground">
            {isConfluence
              ? 'AI-Assist analysiert die Confluence-Seite inkl. Unterseiten und PDF-Attachments und speichert das Ergebnis als Wissenseintrag.'
              : 'AI-Assist recherchiert das Thema und speichert das Ergebnis als Wissenseintrag.'}
          </p>
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="z.B. Wie funktioniert das Authentifizierungs-System?"
            onKeyDown={(e) => e.key === 'Enter' && !isConfluence && handleResearch()}
            autoFocus
          />

          <div className="space-y-2 rounded-md border border-border p-3">
            <p className="text-xs font-medium text-muted-foreground">
              Confluence-Quelle (optional)
            </p>
            <Input
              value={confluenceUrl}
              onChange={(e) => setConfluenceUrl(e.target.value)}
              placeholder="Confluence-Seiten-URL (oder leer lassen)"
            />
            <Input
              value={confluenceSpace}
              onChange={(e) => setConfluenceSpace(e.target.value)}
              placeholder="Space-Key (optional, z.B. DOCS)"
            />
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={includeChildren}
                onChange={(e) => setIncludeChildren(e.target.checked)}
                disabled={!isConfluence}
                className="h-4 w-4 rounded border-border"
              />
              Unterseiten einbeziehen
            </label>
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
          {researching && (
            <p className="text-sm text-muted-foreground animate-pulse">
              {isConfluence
                ? 'Confluence-Recherche läuft... Seitenbaum + PDFs werden analysiert, das kann einige Minuten dauern.'
                : 'Recherche läuft... Dies kann einen Moment dauern.'}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={researching}>
            Abbrechen
          </Button>
          <Button onClick={handleResearch} disabled={!topic.trim() || researching}>
            {researching ? 'Recherchiere...' : 'Recherchieren'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
