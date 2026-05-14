import { useState } from 'react'
import { useSynapseStore } from '@/stores/synapseStore'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import {
  RELATION_LABELS,
  RELATION_COLORS,
  VERDICT_LABELS,
  CONFIDENCE_BAND_LABELS,
} from '@/lib/types'
import type { Synapse, ConfidenceBand } from '@/lib/types'

interface SynapseCardProps {
  projectId: string
  synapse: Synapse
}

const BAND_CLASSES: Record<ConfidenceBand, string> = {
  high: 'bg-green-500/15 text-green-400 ring-green-500/30',
  medium: 'bg-amber-500/15 text-amber-400 ring-amber-500/30',
  low: 'bg-red-500/15 text-red-400 ring-red-500/30',
}

export function SynapseCard({ projectId, synapse }: SynapseCardProps) {
  const detail = useSynapseStore((s) => s.detailById[synapse.id])
  const fetchDetail = useSynapseStore((s) => s.fetchDetail)
  const deleteSynapse = useSynapseStore((s) => s.deleteSynapse)

  const [expanded, setExpanded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const handleToggle = () => {
    const next = !expanded
    setExpanded(next)
    if (next && !detail) {
      void fetchDetail(projectId, synapse.id)
    }
  }

  const confidencePct = Math.round(synapse.confidence * 100)

  return (
    <Card size="sm" className="gap-2">
      <div className="flex items-start gap-2 px-3">
        <button
          onClick={handleToggle}
          className="mt-0.5 text-xs text-muted-foreground hover:text-foreground"
          aria-label={expanded ? 'Einklappen' : 'Ausklappen'}
        >
          {expanded ? '▼' : '▶'}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-medium">{synapse.title}</h3>
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ${BAND_CLASSES[synapse.confidence_band]}`}
              title={CONFIDENCE_BAND_LABELS[synapse.confidence_band]}
            >
              {confidencePct}%
            </span>
            <Badge
              variant={synapse.verdict === 'persist' ? 'secondary' : 'outline'}
              className="shrink-0 text-[10px]"
            >
              {VERDICT_LABELS[synapse.verdict]}
            </Badge>
          </div>
          <p
            className={`mt-1 text-xs text-muted-foreground ${expanded ? '' : 'line-clamp-2'}`}
          >
            {synapse.summary_plain}
          </p>
        </div>

        <Button
          variant="ghost"
          size="sm"
          className="h-6 shrink-0 px-1 text-[10px] text-destructive"
          onClick={() => setConfirmDelete(true)}
          aria-label="Synapse löschen"
        >
          ✕
        </Button>
      </div>

      {expanded && (
        <div className="space-y-2 px-3 pb-1">
          {/* Defects */}
          {detail && detail.defects.length > 0 && (
            <div className="rounded border border-amber-500/20 bg-amber-500/10 p-2 text-[11px] text-amber-400">
              {detail.defects.map((d, i) => (
                <div key={i}>⚠ {d}</div>
              ))}
            </div>
          )}

          {/* Claims with evidence trail */}
          <div className="space-y-1.5">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Aussagen ({synapse.claim_count})
            </span>
            {!detail && (
              <p className="text-[11px] text-muted-foreground">Lädt…</p>
            )}
            {detail?.claims.map((claim) => (
              <div
                key={claim.id}
                className="rounded border border-border/60 bg-muted/30 p-2 text-[11px]"
              >
                <div className="flex items-start gap-1.5">
                  <span
                    className="mt-0.5 shrink-0 rounded px-1 py-0.5 text-[9px] font-medium"
                    style={{
                      backgroundColor: `${RELATION_COLORS[claim.relation]}22`,
                      color: RELATION_COLORS[claim.relation],
                    }}
                  >
                    {RELATION_LABELS[claim.relation]}
                  </span>
                  <span className="flex-1">{claim.claim_text}</span>
                  <span
                    className="shrink-0 text-muted-foreground"
                    title="Verifier-Übereinstimmung"
                  >
                    {Math.round(claim.verifier_agreement * 100)}%
                  </span>
                </div>
                {claim.evidence.length > 0 && (
                  <div className="mt-1 space-y-0.5 border-l-2 border-border pl-2 text-muted-foreground">
                    {claim.evidence.map((ev, i) => (
                      <div key={i} className="italic">
                        „{ev.span}"
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="text-[10px] text-muted-foreground">
            {synapse.source_item_ids.length} Quell-Einträge ·{' '}
            {synapse.source_entity_ids.length} Konzepte
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Synapse löschen"
        description="Die Synapse und ihre validierten Aussagen werden entfernt. Die Quell-Wissenseinträge bleiben unberührt."
        confirmLabel="Löschen"
        onConfirm={() => deleteSynapse(projectId, synapse.id)}
      />
    </Card>
  )
}
