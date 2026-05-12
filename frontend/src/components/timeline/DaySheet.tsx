import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { TimelineItemCard } from './shared/TimelineItemCard'
import type { TimelineItem } from '@/lib/timeline'

interface Props {
  open: boolean
  date: Date | null
  items: readonly TimelineItem[]
  now?: Date
  onClose: () => void
  onItemClick?: (item: TimelineItem) => void
}

export function DaySheet({ open, date, items, now = new Date(), onClose, onItemClick }: Props) {
  const formattedDate = date
    ? date.toLocaleDateString('de-DE', {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      })
    : ''

  const todoCount = items.filter((i) => i.kind === 'todo').length
  const noteCount = items.filter((i) => i.kind === 'note').length

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <SheetContent side="right" className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{formattedDate || 'Tag'}</SheetTitle>
          <SheetDescription>
            {items.length === 0
              ? 'Keine Einträge'
              : `${items.length} Einträge · ${todoCount} Todos · ${noteCount} Notizen`}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {items.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              Kein Todo oder Notiz für diesen Tag.
            </div>
          ) : (
            <ul role="list" className="space-y-2">
              {items.map((item) => (
                <li key={`${item.kind}-${item.id}`}>
                  <TimelineItemCard item={item} now={now} onClick={onItemClick} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
