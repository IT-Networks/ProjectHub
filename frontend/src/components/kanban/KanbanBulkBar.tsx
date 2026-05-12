import { Trash2, X, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { STATUS_LABELS, type TodoStatus } from '@/lib/types'

interface Props {
  count: number
  onMoveTo: (status: TodoStatus) => void
  onDelete: () => void
  onClear: () => void
}

const TARGETS: TodoStatus[] = ['backlog', 'in_progress', 'review', 'done']

export function KanbanBulkBar({ count, onMoveTo, onDelete, onClear }: Props) {
  if (count === 0) return null

  return (
    <div
      role="toolbar"
      aria-label={`${count} ausgewählt`}
      className="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-border bg-popover px-3 py-2 shadow-xl backdrop-blur"
      style={{ animation: 'vt-fade-in var(--motion-duration-fast) var(--motion-ease-out)' }}
    >
      <span className="pl-1 pr-2 text-sm font-medium tabular-nums">
        {count} ausgewählt
      </span>

      <span className="mx-1 h-5 w-px bg-border" aria-hidden />

      <div className="flex items-center gap-1">
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <ArrowRight className="h-3 w-3" /> Verschieben:
        </span>
        {TARGETS.map((s) => (
          <Button
            key={s}
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => onMoveTo(s)}
          >
            {STATUS_LABELS[s]}
          </Button>
        ))}
      </div>

      <span className="mx-1 h-5 w-px bg-border" aria-hidden />

      <Button
        size="sm"
        variant="ghost"
        className="h-7 px-2 text-red-500 hover:bg-red-500/10 hover:text-red-500"
        onClick={onDelete}
      >
        <Trash2 className="h-3.5 w-3.5" />
        <span className="ml-1 text-xs">Löschen</span>
      </Button>

      <Button
        size="sm"
        variant="ghost"
        className="h-7 w-7 p-0"
        aria-label="Auswahl aufheben"
        onClick={onClear}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  )
}
