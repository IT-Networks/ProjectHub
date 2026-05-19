import { useEffect, useMemo } from 'react'
import { useResearchStore } from '@/stores/researchStore'
import { useSSEEvent } from '@/hooks/useSSE'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { FindingsStream } from './FindingsStream'
import { BudgetBar } from './BudgetBar'
import {
  RESEARCH_RUN_PHASE_LABELS,
  type ResearchFinding,
  type ResearchRunPhase,
} from '@/lib/types'

/**
 * ResearchAutoBar — Live-view for one active Auto-Mode run.
 *
 * Mounts as a sibling of the Research-Card in the Knowledge tab; only
 * rendered while ``activeRunByProject[pid]`` exists. SSE listeners
 * (research_progress / _finding / _budget / _lateral_planned /
 * _complete / _subquery_started) update the live store records.
 */
interface ResearchAutoBarProps {
  projectId: string
}

const PHASE_ORDER: ResearchRunPhase[] = [
  'planning',
  'searching',
  'extracting',
  'lateral',
  'validating',
  'persisting',
  'synthesising',
  'done',
]

interface ProgressEvent {
  project_id: string
  run_id: string
  phase: ResearchRunPhase
  hop?: number
  current?: number
  total?: number
}

interface FindingEvent {
  project_id: string
  run_id: string
  sub_query_id: string
  provider_key: string
  source_ref: string
  title: string
  snippet: string
  confidence: number | null
}

interface BudgetEvent {
  project_id: string
  run_id: string
  level: string
  used: number
  hard_cap: number
  by_category: Record<string, number>
  degradations_triggered: string[]
}

interface CompleteEvent {
  project_id: string
  run_id: string
  status: 'ok' | 'partial' | 'error' | 'cancelled'
  token_usage?: Record<string, unknown>
  synapse_run_id?: string
}

interface LateralPlannedEvent {
  project_id: string
  run_id: string
  hop: number
  entities: string[]
  new_sub_queries: Array<{
    id: string
    question: string
    entity_focus?: string
  }>
}

export function ResearchAutoBar({ projectId }: ResearchAutoBarProps) {
  const active = useResearchStore((s) => s.activeRunByProject[projectId])
  const fetchActive = useResearchStore((s) => s.fetchActive)
  const fetchDetail = useResearchStore((s) => s.fetchDetail)
  const cancelRun = useResearchStore((s) => s.cancelRun)
  const pushFinding = useResearchStore((s) => s.pushFinding)
  const setLivePhase = useResearchStore((s) => s.setLivePhase)
  const setLivePressure = useResearchStore((s) => s.setLivePressure)
  const resetLive = useResearchStore((s) => s.resetLive)

  // Reactive run-id; subscribe only when there's an active run.
  const runId = active?.id ?? null

  // Live state (only set after the run starts).
  const livePhase = useResearchStore((s) =>
    runId ? s.livePhase[runId] : undefined,
  )
  const liveCounts = useResearchStore((s) =>
    runId ? s.liveCounts[runId] : undefined,
  )

  useEffect(() => {
    void fetchActive(projectId)
  }, [projectId, fetchActive])

  // SSE wiring — filter by project_id + run_id to avoid cross-talk.
  useSSEEvent('research_progress', (data) => {
    const e = data as ProgressEvent
    if (e.project_id !== projectId || !runId || e.run_id !== runId) return
    setLivePhase(runId, e.phase)
  })

  useSSEEvent('research_finding', (data) => {
    const e = data as FindingEvent
    if (e.project_id !== projectId || !runId || e.run_id !== runId) return
    pushFinding(runId, {
      id: `live-${e.source_ref}-${Date.now()}`,
      sub_query_id: e.sub_query_id,
      provider_key: e.provider_key,
      source_ref: e.source_ref,
      title: e.title,
      snippet: e.snippet,
      url: null,
      timestamp: null,
      author: null,
      status: 'candidate',
      confidence: e.confidence,
      knowledge_item_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    })
  })

  useSSEEvent('research_budget', (data) => {
    const e = data as BudgetEvent
    if (e.project_id !== projectId || !runId || e.run_id !== runId) return
    setLivePressure(runId, e.level)
  })

  useSSEEvent('research_lateral_planned', (data) => {
    const e = data as LateralPlannedEvent
    if (e.project_id !== projectId || !runId || e.run_id !== runId) return
    // Phase transition is also emitted as a research_progress event; we
    // just nudge the phase here so the UI flicks into "lateral" instantly.
    setLivePhase(runId, 'lateral')
  })

  useSSEEvent('research_complete', (data) => {
    const e = data as CompleteEvent
    if (e.project_id !== projectId || !runId || e.run_id !== runId) return
    setLivePhase(runId, 'done')
    // Re-pull the truth: the detail endpoint has the canonical finding
    // statuses (the live-feed Findings were marked "candidate" before
    // validation ran on the backend).
    void fetchDetail(runId)
    void fetchActive(projectId)
  })

  const handleCancel = async () => {
    if (!runId) return
    await cancelRun(runId)
    setLivePhase(runId, 'done')
  }

  // Phase-stepper UI helpers.
  const currentPhaseIdx = useMemo(() => {
    const phase = livePhase ?? active?.phase ?? 'planning'
    const idx = PHASE_ORDER.indexOf(phase as ResearchRunPhase)
    return idx >= 0 ? idx : 0
  }, [livePhase, active])

  if (!active) return null

  const phase = (livePhase ?? active.phase) as ResearchRunPhase

  return (
    <div
      className="rounded-md border bg-card p-4 space-y-3"
      data-testid="research-auto-bar"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <Badge variant="default">
              {active.depth === 'tief' ? '🔍🔍 Tief' : '🔍 Normal'}
            </Badge>
            <span className="font-mono text-sm">{active.topic.slice(0, 80)}</span>
          </div>
          <span className="text-xs text-muted-foreground">
            Run {active.id.slice(0, 8)} · Status:{' '}
            <span data-testid="active-status">{active.status}</span>
          </span>
        </div>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => void handleCancel()}
          disabled={active.status !== 'running'}
          data-testid="cancel-run"
        >
          Abbrechen
        </Button>
      </div>

      {/* Phase Stepper */}
      <div className="flex items-center gap-1 overflow-x-auto" data-testid="phase-stepper">
        {PHASE_ORDER.map((p, i) => {
          const reached = i <= currentPhaseIdx
          const isCurrent = i === currentPhaseIdx
          return (
            <div key={p} className="flex items-center gap-1">
              <span
                className={`text-xs px-2 py-0.5 rounded ${
                  isCurrent
                    ? 'bg-primary text-primary-foreground font-semibold'
                    : reached
                      ? 'bg-muted text-muted-foreground'
                      : 'text-muted-foreground opacity-50'
                }`}
              >
                {RESEARCH_RUN_PHASE_LABELS[p]}
              </span>
              {i < PHASE_ORDER.length - 1 && (
                <span className="text-muted-foreground text-xs">→</span>
              )}
            </div>
          )
        })}
      </div>

      {/* Live counters */}
      <div className="flex gap-3 text-xs" data-testid="live-counters">
        <span>
          Findings: <strong>{liveCounts?.findings ?? 0}</strong>
        </span>
        <span>
          Persistiert: <strong>{liveCounts?.persisted ?? 0}</strong>
        </span>
        <span>
          Flagged: <strong>{liveCounts?.flagged ?? 0}</strong>
        </span>
        <span>
          Rejected: <strong>{liveCounts?.rejected ?? 0}</strong>
        </span>
        {phase === 'lateral' && (
          <Badge variant="secondary" className="text-xs">
            Lateral Hop {active.current_hop}
          </Badge>
        )}
      </div>

      {/* Budget bar */}
      {runId && <BudgetBar runId={runId} />}

      {/* Live findings feed */}
      {runId && <FindingsStream runId={runId} projectId={projectId} />}
    </div>
  )
}
