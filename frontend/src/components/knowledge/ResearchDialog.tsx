import { useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { useResearchStore } from '@/stores/researchStore'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ResearchDepth } from '@/lib/types'

interface ResearchDialogProps {
  projectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

type RunMode = 'single' | 'auto'

export function ResearchDialog({ projectId, open, onOpenChange }: ResearchDialogProps) {
  const researchTopic = useKnowledgeStore((s) => s.researchTopic)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const fetchStats = useKnowledgeStore((s) => s.fetchStats)
  const startAutoRun = useResearchStore((s) => s.startRun)

  const [topic, setTopic] = useState('')
  const [mode, setMode] = useState<RunMode>('auto')
  const [depth, setDepth] = useState<ResearchDepth>('normal')
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
    setMode('auto')
    setDepth('normal')
  }

  const handleResearch = async () => {
    if (!topic.trim()) return
    setResearching(true)
    setError(null)
    try {
      if (mode === 'auto' && !isConfluence) {
        // Auto-Mode: trigger the new pipeline, close dialog immediately,
        // ResearchAutoBar takes over showing live progress.
        const res = await startAutoRun(projectId, { topic, depth, mode: 'auto' })
        if (res && res.started) {
          onOpenChange(false)
          reset()
        } else if (res && !res.started && res.reason === 'already_running') {
          setError(
            'Bereits ein Recherche-Lauf in diesem Projekt aktiv. Bitte abwarten oder abbrechen.',
          )
        }
      } else {
        // Single-Shot (legacy / Confluence Deep-Research): blocking call.
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
      }
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
              : mode === 'auto'
                ? 'Auto-Mode: parallel mehrere Quellen, live Stream, validiert vor dem Speichern.'
                : 'Single-Shot: ein einzelner Agent-Call (Legacy-Pfad).'}
          </p>
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="z.B. Wie funktioniert das Authentifizierungs-System?"
            onKeyDown={(e) => e.key === 'Enter' && !isConfluence && handleResearch()}
            autoFocus
          />

          {/* Mode + Depth toggles (hidden in Confluence Deep-Research path) */}
          {!isConfluence && (
            <div className="space-y-2 rounded-md border border-border p-3">
              <div className="flex items-center gap-3 text-sm">
                <span className="font-medium">Modus:</span>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="mode"
                    checked={mode === 'auto'}
                    onChange={() => setMode('auto')}
                    data-testid="mode-auto"
                  />
                  Auto
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="mode"
                    checked={mode === 'single'}
                    onChange={() => setMode('single')}
                    data-testid="mode-single"
                  />
                  Single-Shot
                </label>
              </div>
              {mode === 'auto' && (
                <div className="flex items-center gap-3 text-sm">
                  <span className="font-medium">Tiefe:</span>
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="depth"
                      checked={depth === 'normal'}
                      onChange={() => setDepth('normal')}
                      data-testid="depth-normal"
                    />
                    Normal
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="depth"
                      checked={depth === 'tief'}
                      onChange={() => setDepth('tief')}
                      data-testid="depth-tief"
                    />
                    Tief (links/rechts schauen)
                  </label>
                </div>
              )}
            </div>
          )}

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
