import { Rows2, Rows3, Rows4 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { KanbanDensity } from '@/stores/todoStore'
import { cn } from '@/lib/utils'

const OPTIONS: { value: KanbanDensity; label: string; Icon: typeof Rows2 }[] = [
  { value: 'compact', label: 'Kompakt', Icon: Rows4 },
  { value: 'comfortable', label: 'Komfortabel', Icon: Rows3 },
  { value: 'spacious', label: 'Luftig', Icon: Rows2 },
]

interface Props {
  value: KanbanDensity
  onChange: (v: KanbanDensity) => void
}

export function DensityToggle({ value, onChange }: Props) {
  return (
    <div
      role="radiogroup"
      aria-label="Dichte"
      className="inline-flex items-center rounded-md border border-border bg-muted/30 p-0.5"
    >
      {OPTIONS.map(({ value: v, label, Icon }) => (
        <Button
          key={v}
          role="radio"
          aria-checked={value === v}
          size="sm"
          variant="ghost"
          title={label}
          onClick={() => onChange(v)}
          className={cn(
            'h-7 w-7 p-0 transition-colors',
            value === v ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </Button>
      ))}
    </div>
  )
}
