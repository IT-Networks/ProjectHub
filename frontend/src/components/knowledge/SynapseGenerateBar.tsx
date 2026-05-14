import { useEffect, useState } from 'react'
import { useSynapseStore } from '@/stores/synapseStore'
import { useSSEEvent } from '@/hooks/useSSE'
import { Button } from '@/components/ui/button'
import { RUN_PHASE_LABELS } from '@/lib/types'
import type { RunPhase } from '@/lib/types'

interface SynapseGenerateBarProps {
  projectId: string
}

interface ProgressEvent {
  project_id: string
  run_id: string
  phase: RunPhase
  communities?: number
  current?: number
  total?: number
}

interface CompleteEvent {
  project_id: string
  run_id: string
  status: 'ok' | 'error'
  error?: string
}

const PHASE_ORDER: RunPhase[] = [
  'extracting_entities',
  'detecting_communities',
  'synthesising',
  'validating',
  'done',
]

export function SynapseGenerateBar({ projectId }: SynapseGenerateBarProps) {
  const latestRun = useSynapseStore((s) => s.latestRunByProject[projectId])
  const fetchLatestRun = useSynapseStore((s) => s.fetchLatestRun)
  const generate = useSynapseStore((s) => s.generate)
  const onComplete = useSynapseStore((s) => s.onComplete)

  const [localGenerating, setLocalGenerating] = useState(false)
  const [progress, setProgress] = useState<ProgressEvent | null>(null)

  useEffect(() => {
    void fetchLatestRun(projectId)
  }, [projectId, fetchLatestRun])

  const generating = localGenerating || latestRun?.status === 'running'

  useSSEEvent('synapse_progress', (data: ProgressEvent) => {
    if (data.project_id === projectId) setProgress(data)
  })

  useSSEEvent('synapse_complete', (data: CompleteEvent) => {
    if (data.project_id !== projectId) return
    setLocalGenerating(false)
    setProgress(null)
    void onComplete(projectId)
  })

  const handleGenerate = async () => {
    setLocalGenerating(true)
    setProgress(null)
    const res = await generate(projectId)
    // Not actually started (e.g. already running) → drop the optimistic flag.
    if (!res?.started) setLocalGenerating(false)
  }

  const phaseIndex = progress
    ? PHASE_ORDER.indexOf(progress.phase)
    : -1

  return (
    <div className="mb-4 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">Wissens-Synthese</span>
            <span className="text-xs text-muted-foreground">
              Verdichtet das Projektwissen zu validierten Synapsen
            </span>
          </div>

          {/* Idle: last run summary */}
          {!generating && latestRun && latestRun.status !== 'running' && (
            <p className="mt-1 text-xs text-muted-foreground">
              {latestRun.status === 'error' ? (
                <span className="text-red-400">
                  Letzter Lauf fehlgeschlagen: {latestRun.error_summary}
                </span>
              ) : (
                <>
                  Letzter Lauf: {latestRun.synapse_count} Synapse(n) ·{' '}
                  {latestRun.validated_count} validiert ·{' '}
                  {latestRun.flagged_count} ungeprüft ·{' '}
                  {latestRun.review_count} im Review
                  {latestRun.token_usage?.total_tokens
                    ? ` · ${latestRun.token_usage.total_tokens.toLocaleString('de-DE')} Tokens`
                    : ''}
                </>
              )}
            </p>
          )}

          {!generating && !latestRun && (
            <p className="mt-1 text-xs text-muted-foreground">
              Noch keine Synthese durchgeführt.
            </p>
          )}
        </div>

        <Button size="sm" onClick={handleGenerate} disabled={generating}>
          {generating ? 'Läuft…' : latestRun ? 'Neu synthetisieren' : 'Synthese starten'}
        </Button>
      </div>

      {/* Live progress */}
      {generating && (
        <div className="mt-3 space-y-1.5">
          <div className="flex items-center gap-2 text-xs">
            <span className="animate-spin">⟳</span>
            <span className="font-medium">
              {progress ? RUN_PHASE_LABELS[progress.phase] : 'Wird gestartet…'}
            </span>
            {progress?.phase === 'validating' && progress.total ? (
              <span className="text-muted-foreground">
                Synapse {progress.current}/{progress.total}
              </span>
            ) : null}
            {progress?.phase === 'synthesising' &&
            progress.communities != null ? (
              <span className="text-muted-foreground">
                {progress.communities} Cluster
              </span>
            ) : null}
          </div>
          {/* Phase stepper */}
          <div className="flex gap-1">
            {PHASE_ORDER.slice(0, 4).map((phase, i) => (
              <div
                key={phase}
                className={`h-1 flex-1 rounded-full transition-colors ${
                  phaseIndex >= i ? 'bg-primary' : 'bg-muted'
                }`}
                title={RUN_PHASE_LABELS[phase]}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
