import { Trash2, Copy, Archive, Tag, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface BatchAction {
  id: string
  label: string
  icon: React.ReactNode
  onClick: () => void
  variant?: 'default' | 'destructive' | 'secondary'
  disabled?: boolean
}

interface BatchActionsToolbarProps {
  selectedCount: number
  totalCount: number
  onClearSelection: () => void
  actions: BatchAction[]
  compact?: boolean
  className?: string
}

export function BatchActionsToolbar({
  selectedCount,
  totalCount,
  onClearSelection,
  actions,
  compact = false,
  className,
}: BatchActionsToolbarProps) {
  if (selectedCount === 0) return null

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-lg border border-border bg-accent/50 px-4 py-2 transition-all',
        compact && 'py-1.5 px-3',
        className
      )}
    >
      {/* Selection Counter */}
      <div className="flex-1 text-sm font-medium">
        <span className="text-foreground">
          {selectedCount} von {totalCount}
        </span>
        <span className="ml-1 text-muted-foreground">
          ausgewählt
        </span>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1">
        {actions.map((action) => (
          <Button
            key={action.id}
            variant={action.variant || 'secondary'}
            size={compact ? 'sm' : 'sm'}
            onClick={action.onClick}
            disabled={action.disabled}
            title={action.label}
            className="gap-1.5"
          >
            {action.icon}
            {!compact && <span className="text-xs">{action.label}</span>}
          </Button>
        ))}
      </div>

      {/* Clear Selection */}
      <Button
        variant="ghost"
        size={compact ? 'sm' : 'sm'}
        onClick={onClearSelection}
        title="Auswahl löschen"
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  )
}
