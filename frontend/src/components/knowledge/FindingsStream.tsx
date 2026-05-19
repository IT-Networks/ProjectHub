import { useState } from 'react'
import { useResearchStore } from '@/stores/researchStore'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { ResearchFinding, ResearchFindingStatus } from '@/lib/types'

/**
 * FindingsStream — live chronological feed of findings for one run.
 *
 * Reads ``liveFindings[runId]`` (populated by SSE) AND the persisted
 * detail (after research_complete fires + fetchDetail runs). Each row
 * shows title, snippet, provider, confidence, status badge plus
 * accept/reject buttons for flagged/candidate items.
 */
interface FindingsStreamProps {
  runId: string
  projectId: string
}

const STATUS_VARIANT: Record<
  ResearchFindingStatus,
  'default' | 'secondary' | 'destructive' | 'outline'
> = {
  candidate: 'outline',
  grounded: 'secondary',
  persisted: 'default',
  flagged: 'destructive',
  rejected: 'destructive',
  failed: 'destructive',
  cancelled: 'outline',
  blocked: 'destructive',
}

const STATUS_LABEL: Record<ResearchFindingStatus, string> = {
  candidate: 'kandidat',
  grounded: 'grounded',
  persisted: 'gespeichert',
  flagged: 'review',
  rejected: 'verworfen',
  failed: 'fehler',
  cancelled: 'abgebrochen',
  blocked: 'blockiert',
}

export function FindingsStream({ runId, projectId: _projectId }: FindingsStreamProps) {
  const live = useResearchStore((s) => s.liveFindings[runId] ?? [])
  const detail = useResearchStore((s) => s.detailByRun[runId])
  const acceptFinding = useResearchStore((s) => s.acceptFinding)
  const rejectFinding = useResearchStore((s) => s.rejectFinding)

  // Prefer persisted detail when available — its statuses are canonical.
  // Fall back to live feed while the run is still streaming.
  const findings: ResearchFinding[] = detail?.findings ?? live

  const [expandedId, setExpandedId] = useState<string | null>(null)

  if (findings.length === 0) {
    return (
      <div className="text-xs text-muted-foreground italic" data-testid="findings-empty">
        Noch keine Findings.
      </div>
    )
  }

  return (
    <ul className="space-y-1.5 max-h-96 overflow-y-auto" data-testid="findings-stream">
      {findings.map((f) => {
        const isExpanded = expandedId === f.id
        const isActionable = f.status === 'flagged' || f.status === 'candidate'
        return (
          <li
            key={f.id}
            className="rounded border bg-background p-2"
            data-testid={`finding-${f.id}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge
                    variant={STATUS_VARIANT[f.status]}
                    className="text-xs shrink-0"
                  >
                    {STATUS_LABEL[f.status]}
                  </Badge>
                  <span className="text-xs font-mono text-muted-foreground shrink-0">
                    {f.provider_key}
                  </span>
                  {f.confidence !== null && (
                    <span className="text-xs text-muted-foreground shrink-0">
                      conf {f.confidence.toFixed(2)}
                    </span>
                  )}
                  <span className="text-sm font-medium truncate">
                    {f.title || '(ohne Titel)'}
                  </span>
                </div>
                {f.snippet && (
                  <p
                    className={`text-xs text-muted-foreground mt-1 ${
                      isExpanded ? '' : 'line-clamp-2'
                    }`}
                  >
                    {f.snippet}
                  </p>
                )}
                <div className="flex items-center gap-2 mt-1">
                  <button
                    type="button"
                    className="text-xs text-primary hover:underline"
                    onClick={() => setExpandedId(isExpanded ? null : f.id)}
                  >
                    {isExpanded ? 'Weniger' : 'Mehr'}
                  </button>
                  <span className="text-xs font-mono text-muted-foreground truncate">
                    {f.source_ref}
                  </span>
                </div>
              </div>
              {isActionable && (
                <div className="flex flex-col gap-1 shrink-0">
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => void acceptFinding(runId, f.id)}
                    data-testid={`accept-${f.id}`}
                  >
                    Übernehmen
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void rejectFinding(runId, f.id)}
                    data-testid={`reject-${f.id}`}
                  >
                    Verwerfen
                  </Button>
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
