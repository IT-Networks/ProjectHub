import { cn } from '@/lib/utils'

interface SkeletonProps {
  /**
   * Custom CSS classes
   */
  className?: string

  /**
   * Width of the skeleton
   */
  width?: string | number

  /**
   * Height of the skeleton
   */
  height?: string | number
}

/**
 * Skeleton Loader Component
 *
 * Used to show loading state while content is being fetched.
 * Creates a placeholder that animates with a shimmer effect.
 *
 * @example
 * <Skeleton className="h-4 w-3/4" />
 * <Skeleton width={200} height={100} />
 */
export function Skeleton({ className, width, height }: SkeletonProps) {
  return (
    <div
      className={cn(
        'rounded-md bg-gradient-to-r from-muted via-muted-foreground/10 to-muted animate-pulse',
        className,
      )}
      style={{
        width: typeof width === 'number' ? `${width}px` : width,
        height: typeof height === 'number' ? `${height}px` : height,
      }}
      aria-busy="true"
      role="status"
    />
  )
}

/**
 * Card Skeleton - Shows loading state for a card component
 */
export function CardSkeleton({
  lines = 3,
  className,
}: {
  lines?: number
  className?: string
}) {
  return (
    <div className={cn('rounded-lg border border-border p-4 space-y-3', className)}>
      {/* Header skeleton */}
      <div className="space-y-2">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>

      {/* Content skeletons */}
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton
            key={i}
            className={cn('h-4', i === lines - 1 ? 'w-3/4' : 'w-full')}
          />
        ))}
      </div>

      {/* Footer skeleton */}
      <div className="flex gap-2 pt-2">
        <Skeleton className="h-8 w-20" />
        <Skeleton className="h-8 w-20" />
      </div>
    </div>
  )
}

/**
 * List Skeleton - Shows loading state for a list of items
 */
export function ListSkeleton({
  count = 3,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-md border border-border p-3 space-y-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  )
}

/**
 * Table Skeleton - Shows loading state for a table
 */
export function TableSkeleton({
  rows = 5,
  columns = 4,
  className,
}: {
  rows?: number
  columns?: number
  className?: string
}) {
  return (
    <div className={cn('space-y-2', className)}>
      {/* Header */}
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}>
        {Array.from({ length: columns }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>

      {/* Rows */}
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div
          key={rowIdx}
          className="grid gap-2"
          style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}
        >
          {Array.from({ length: columns }).map((_, colIdx) => (
            <Skeleton key={colIdx} className="h-10 w-full" />
          ))}
        </div>
      ))}
    </div>
  )
}

/**
 * Avatar Skeleton - Shows loading state for an avatar
 */
export function AvatarSkeleton({ size = 40 }: { size?: number }) {
  return (
    <Skeleton
      className="rounded-full"
      width={size}
      height={size}
    />
  )
}

/**
 * Dashboard Widget Skeleton - Shows loading state for a dashboard widget
 */
export function WidgetSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn('rounded-lg border border-border p-4 space-y-3', className)}>
      {/* Widget header */}
      <div className="flex items-center justify-between">
        <Skeleton className="h-5 w-2/5" />
        <Skeleton className="h-4 w-12" />
      </div>

      {/* Widget content (varied heights for visual interest) */}
      <div className="space-y-3 pt-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>

      {/* Widget footer */}
      <div className="flex gap-2 pt-2">
        <Skeleton className="h-3 w-1/4" />
        <Skeleton className="h-3 w-1/4" />
      </div>
    </div>
  )
}

/**
 * Kanban Board Skeleton - Shows loading state for kanban board
 */
export function KanbanSkeleton({ columns = 4, cardsPerColumn = 3, className }: { columns?: number; cardsPerColumn?: number; className?: string }) {
  return (
    <div className={cn('flex gap-4 overflow-x-auto pb-4', className)}>
      {Array.from({ length: columns }).map((_, colIdx) => (
        <div key={colIdx} className="flex min-w-[280px] flex-col gap-3">
          {/* Column header */}
          <div className="flex items-center gap-2">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-5 w-8 rounded-full" />
          </div>

          {/* Column cards */}
          <div className="flex flex-col gap-2">
            {Array.from({ length: cardsPerColumn }).map((_, cardIdx) => (
              <div
                key={cardIdx}
                className="rounded-lg border border-border p-3 space-y-2"
              >
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
                <div className="flex gap-1 pt-1">
                  <Skeleton className="h-5 w-12" />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * Shimmer Loading (full-screen loader)
 */
export function ShimmerLoader({ message = 'Loading...' }: { message?: string }) {
  return (
    <div className="flex items-center justify-center h-screen">
      <div className="text-center space-y-4">
        <div className="flex gap-2 justify-center">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="w-3 h-3 rounded-full bg-primary animate-bounce"
              style={{ animationDelay: `${i * 100}ms` }}
            />
          ))}
        </div>
        <p className="text-sm text-muted-foreground">{message}</p>
      </div>
    </div>
  )
}
