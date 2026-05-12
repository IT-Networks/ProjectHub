import { AlertTriangle, Sun, CalendarDays, CalendarRange, Calendar, Clock, Circle, CheckCircle2 } from 'lucide-react'
import type { BucketId, BucketTone } from '@/lib/timeline'
import { cn } from '@/lib/utils'

const ICONS: Record<BucketId, typeof Sun> = {
  overdue: AlertTriangle,
  today: Sun,
  tomorrow: CalendarDays,
  this_week: CalendarRange,
  next_week: CalendarRange,
  this_month: Calendar,
  later: Clock,
  undated: Circle,
  completed: CheckCircle2,
}

const TONE_CLASS: Record<BucketTone, string> = {
  danger: 'text-red-500',
  brand: 'text-brand',
  neutral: 'text-foreground/70',
  muted: 'text-muted-foreground',
  success: 'text-emerald-500',
}

interface Props {
  id: BucketId
  tone: BucketTone
  className?: string
}

export function BucketIcon({ id, tone, className }: Props) {
  const Icon = ICONS[id]
  return <Icon aria-hidden className={cn('h-4 w-4', TONE_CLASS[tone], className)} />
}
