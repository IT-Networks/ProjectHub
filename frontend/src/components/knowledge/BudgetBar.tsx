import { useResearchStore } from '@/stores/researchStore'
import { Badge } from '@/components/ui/badge'

/**
 * BudgetBar — token-pressure indicator for one active research run.
 *
 * Reads from ``livePressure`` (set by SSE research_budget events).
 * No raw token numbers here — the per-category breakdown lives in the
 * run-detail view; this bar is the "traffic light" in the live header.
 */
interface BudgetBarProps {
  runId: string
}

const LEVEL_LABEL: Record<string, string> = {
  ok: 'Budget ok',
  warn: 'Budget warn',
  tight: 'Budget knapp',
  critical: 'Budget kritisch',
  extreme: 'Budget extrem',
  exhausted: 'Budget aufgebraucht',
}

const LEVEL_VARIANT: Record<
  string,
  'default' | 'secondary' | 'destructive' | 'outline'
> = {
  ok: 'outline',
  warn: 'secondary',
  tight: 'secondary',
  critical: 'destructive',
  extreme: 'destructive',
  exhausted: 'destructive',
}

const LEVEL_BAR_COLOR: Record<string, string> = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-400',
  tight: 'bg-amber-500',
  critical: 'bg-orange-500',
  extreme: 'bg-red-500',
  exhausted: 'bg-red-700',
}

const LEVEL_BAR_WIDTH: Record<string, string> = {
  ok: 'w-1/6',
  warn: 'w-2/6',
  tight: 'w-3/6',
  critical: 'w-4/6',
  extreme: 'w-5/6',
  exhausted: 'w-full',
}

export function BudgetBar({ runId }: BudgetBarProps) {
  const level = useResearchStore((s) => s.livePressure[runId]) ?? 'ok'

  return (
    <div className="flex items-center gap-2" data-testid="budget-bar">
      <Badge variant={LEVEL_VARIANT[level] ?? 'outline'} className="text-xs">
        {LEVEL_LABEL[level] ?? level}
      </Badge>
      <div
        className="h-1.5 flex-1 rounded bg-muted overflow-hidden"
        role="progressbar"
        aria-label="Token-Budget"
      >
        <div
          className={`h-full transition-all ${LEVEL_BAR_COLOR[level] ?? 'bg-muted-foreground'} ${LEVEL_BAR_WIDTH[level] ?? 'w-0'}`}
          data-testid={`budget-bar-fill-${level}`}
        />
      </div>
    </div>
  )
}
