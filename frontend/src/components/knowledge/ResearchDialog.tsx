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
  const [researching, setResearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleResearch = async () => {
    if (!topic.trim()) return
    setResearching(true)
    setError(null)
    try {
      await researchTopic(projectId, topic)
      onOpenChange(false)
      setTopic('')
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
            AI-Assist recherchiert das Thema und speichert das Ergebnis als Wissenseintrag.
          </p>
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="z.B. Wie funktioniert das Authentifizierungs-System?"
            onKeyDown={(e) => e.key === 'Enter' && handleResearch()}
            autoFocus
          />
          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
          {researching && (
            <p className="text-sm text-muted-foreground animate-pulse">
              Recherche läuft... Dies kann einen Moment dauern.
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
