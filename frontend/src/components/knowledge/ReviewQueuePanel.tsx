import { useEffect, useState } from 'react'
import { useSynapseStore } from '@/stores/synapseStore'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

interface ReviewQueuePanelProps {
  projectId: string
}

/**
 * Review-Queue — Synapsen, die die Validierung als unsicher eingestuft hat
 * (niedrige Konfidenz oder ein widersprochener Claim). Der Mensch fällt
 * hier das letzte Urteil.
 */
export function ReviewQueuePanel({ projectId }: ReviewQueuePanelProps) {
  const queue = useSynapseStore((s) => s.reviewQueueByProject[projectId])
  const fetchReviewQueue = useSynapseStore((s) => s.fetchReviewQueue)
  const reviewSynapse = useSynapseStore((s) => s.reviewSynapse)

  const [expanded, setExpanded] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)

  useEffect(() => {
    void fetchReviewQueue(projectId)
  }, [projectId, fetchReviewQueue])

  const open = (queue ?? []).filter((item) => item.human_verdict === null)
  if (open.length === 0) return null

  const handleVerdict = async (
    synapseId: string,
    verdict: 'accepted' | 'rejected',
  ) => {
    setBusyId(synapseId)
    try {
      await reviewSynapse(projectId, synapseId, verdict)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="mb-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-left text-sm transition-colors hover:bg-amber-500/15"
      >
        <span className="text-xs">{expanded ? '▼' : '▶'}</span>
        <span className="font-medium text-amber-400">Review erforderlich</span>
        <Badge variant="outline" className="ml-auto text-xs">
          {open.length}
        </Badge>
      </button>

      {expanded && (
        <div className="mt-2 space-y-1.5">
          {open.map((item) => (
            <Card
              key={item.id}
              size="sm"
              className="flex-row items-center justify-between gap-3 px-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">
                    {item.synapse_title}
                  </span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {Math.round(item.confidence * 100)}%
                  </span>
                </div>
                <p className="truncate text-xs text-muted-foreground">
                  {item.reason}
                </p>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  disabled={busyId === item.synapse_id}
                  onClick={() => handleVerdict(item.synapse_id, 'accepted')}
                >
                  Übernehmen
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs text-destructive"
                  disabled={busyId === item.synapse_id}
                  onClick={() => handleVerdict(item.synapse_id, 'rejected')}
                >
                  Verwerfen
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
