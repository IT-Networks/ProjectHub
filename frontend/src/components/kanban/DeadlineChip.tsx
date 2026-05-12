import { CalendarClock } from 'lucide-react'
import { cn } from '@/lib/utils'

type Tone = 'overdue' | 'soon' | 'normal' | 'far'

function classify(deadline: Date, now: Date): Tone {
  const diffMs = deadline.getTime() - now.getTime()
  const diffDays = diffMs / 86_400_000
  if (diffDays < 0) return 'overdue'
  if (diffDays < 1) return 'soon'
  if (diffDays < 7) return 'normal'
  return 'far'
}

function formatRelative(deadline: Date, now: Date, locale = 'de-DE'): string {
  const diffMs = deadline.getTime() - now.getTime()
  const diffDays = Math.round(diffMs / 86_400_000)
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (Math.abs(diffDays) <= 7) return rtf.format(diffDays, 'day')
  return deadline.toLocaleDateString(locale, { day: '2-digit', month: '2-digit' })
}

const TONE_CLASS: Record<Tone, string> = {
  overdue: 'text-red-500 bg-red-500/10 border-red-500/30',
  soon: 'text-amber-600 dark:text-amber-400 bg-amber-500/10 border-amber-500/30',
  normal: 'text-foreground/70 bg-muted/50 border-border',
  far: 'text-muted-foreground bg-transparent border-transparent',
}

interface Props {
  deadline: string
  now?: Date
  className?: string
}

export function DeadlineChip({ deadline, now = new Date(), className }: Props) {
  const d = new Date(deadline)
  if (isNaN(d.getTime())) return null
  const tone = classify(d, now)
  const label = formatRelative(d, now)

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium',
        TONE_CLASS[tone],
        className,
      )}
      title={d.toLocaleString('de-DE')}
    >
      <CalendarClock className="h-3 w-3" aria-hidden />
      {label}
    </span>
  )
}
