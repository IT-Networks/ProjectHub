import { useState } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { cn } from '@/lib/utils'
import type { WidgetConfig } from '@/lib/types'

interface Props {
  widget: WidgetConfig
  title: string
  onRemove: () => void
  children: React.ReactNode
}

export function WidgetWrapper({ widget, title, onRemove, children }: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: widget.id,
  })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    gridColumn: `span ${widget.grid_width}`,
    gridRow: `span ${widget.grid_height}`,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'group rounded-lg border border-border bg-card p-4',
        isDragging && 'opacity-50'
      )}
    >
      {/* Header */}
      <div className="mb-3 flex items-center justify-between">
        <h3
          {...attributes}
          {...listeners}
          className="cursor-grab text-sm font-semibold"
        >
          {title}
        </h3>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0 opacity-0 transition-opacity group-hover:opacity-100"
          aria-label="Widget entfernen"
          onClick={() => setConfirmOpen(true)}
        >
          x
        </Button>
      </div>

      {/* Content */}
      {children}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Widget entfernen"
        description={`"${title}" wird vom Dashboard entfernt.`}
        confirmLabel="Entfernen"
        onConfirm={onRemove}
      />
    </div>
  )
}
