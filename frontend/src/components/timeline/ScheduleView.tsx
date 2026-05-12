import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { Bucket, TimelineItem } from '@/lib/timeline'
import { BucketIcon } from './shared/BucketIcon'
import { TimelineItemCard } from './shared/TimelineItemCard'
import { EmptyState } from '@/components/shared/EmptyState'
import { Button } from '@/components/ui/button'
import { useViewTransitionNavigate } from '@/hooks/useViewTransition'
import { cn } from '@/lib/utils'

interface Props {
  buckets: readonly Bucket[]
  now?: Date
  onItemClick?: (item: TimelineItem) => void
}

const TONE_HEADER: Record<Bucket['tone'], string> = {
  danger: 'text-red-500',
  brand: 'text-brand',
  neutral: 'text-foreground',
  muted: 'text-muted-foreground',
  success: 'text-emerald-500',
}

export function ScheduleView({ buckets, now = new Date(), onItemClick }: Props) {
  const sectionRefs = useRef<Map<string, HTMLElement>>(new Map())
  const navigate = useViewTransitionNavigate()

  const bucketIds = useMemo(() => buckets.map((b) => b.id), [buckets])

  const scrollToBucket = useCallback((idx: number) => {
    const id = bucketIds[idx]
    if (!id) return
    const el = sectionRefs.current.get(id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [bucketIds])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      const isFormEl =
        tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement | null)?.isContentEditable
      if (isFormEl) return
      if (e.ctrlKey || e.metaKey || e.altKey) return

      if (e.key === 'j' || e.key === 'k') {
        const scrollY = window.scrollY
        let closestIdx = 0
        let closestDist = Infinity
        bucketIds.forEach((id, idx) => {
          const el = sectionRefs.current.get(id)
          if (!el) return
          const dist = Math.abs(el.offsetTop - scrollY)
          if (dist < closestDist) { closestDist = dist; closestIdx = idx }
        })
        e.preventDefault()
        scrollToBucket(e.key === 'j' ? Math.min(closestIdx + 1, bucketIds.length - 1) : Math.max(closestIdx - 1, 0))
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [bucketIds, scrollToBucket])

  if (buckets.length === 0) {
    return (
      <EmptyState
        icon="📅"
        title="Keine Items mit Fristen"
        description="Todos und Notizen erscheinen hier, sobald Du ihnen eine Deadline gibst. Öffne das Kanban-Board, um Deadlines zu setzen."
        action={<Button onClick={() => navigate('/kanban')}>Zum Kanban</Button>}
        size="spacious"
      />
    )
  }

  return (
    <section aria-label="Zeitplan" className="space-y-6">
      {buckets.map((bucket) => (
        <section
          key={bucket.id}
          ref={(el) => {
            if (el) sectionRefs.current.set(bucket.id, el)
            else sectionRefs.current.delete(bucket.id)
          }}
          aria-labelledby={`bucket-${bucket.id}`}
        >
          <h2
            id={`bucket-${bucket.id}`}
            className={cn(
              'timeline-bucket-header flex items-center gap-2 py-1 text-sm font-semibold tracking-tight',
              TONE_HEADER[bucket.tone],
            )}
          >
            <BucketIcon id={bucket.id} tone={bucket.tone} />
            <span>{bucket.label}</span>
            <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
              {bucket.items.length}
            </span>
          </h2>

          <ul role="list" className="mt-2 space-y-1.5">
            {bucket.items.map((item) => (
              <li key={`${item.kind}-${item.id}`}>
                <TimelineItemCard item={item} onClick={onItemClick} now={now} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </section>
  )
}
