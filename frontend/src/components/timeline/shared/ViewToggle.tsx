import { ListTree, CalendarDays, GanttChartSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { TimelineView } from '@/stores/timelineStore'
import { cn } from '@/lib/utils'

const OPTIONS: { value: TimelineView; label: string; icon: typeof ListTree }[] = [
  { value: 'schedule', label: 'Zeitplan', icon: ListTree },
  { value: 'calendar', label: 'Kalender', icon: CalendarDays },
  { value: 'gantt', label: 'Gantt', icon: GanttChartSquare },
]

interface Props {
  value: TimelineView
  onChange: (v: TimelineView) => void
}

export function ViewToggle({ value, onChange }: Props) {
  return (
    <div
      role="tablist"
      aria-label="Ansicht"
      className="inline-flex items-center rounded-md border border-border bg-muted/30 p-0.5"
    >
      {OPTIONS.map(({ value: v, label, icon: Icon }) => (
        <Button
          key={v}
          role="tab"
          aria-selected={value === v}
          size="sm"
          variant="ghost"
          onClick={() => onChange(v)}
          className={cn(
            'h-7 gap-1.5 px-2.5 text-xs transition-colors',
            value === v ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
        </Button>
      ))}
    </div>
  )
}
