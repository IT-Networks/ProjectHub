import { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { SPACING } from '@/lib/design-system'

interface EmptyStateProps {
  /**
   * Icon to display (emoji, SVG, or React component)
   */
  icon?: ReactNode

  /**
   * Main heading text
   */
  title: string

  /**
   * Optional description text
   */
  description?: string | ReactNode

  /**
   * Optional action button(s) or custom action element
   */
  action?: ReactNode

  /**
   * Size variant
   */
  size?: 'compact' | 'normal' | 'spacious'

  /**
   * Additional CSS classes
   */
  className?: string
}

/**
 * EmptyState Component
 *
 * Displays a friendly message when a list, table, or content area is empty.
 * Helps users understand why content is missing and what to do next.
 *
 * @example
 * <EmptyState
 *   icon="📭"
 *   title="No projects yet"
 *   description="Create your first project to get started"
 *   action={<Button onClick={handleCreate}>New Project</Button>}
 * />
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  size = 'normal',
  className,
}: EmptyStateProps) {
  const paddingMap = {
    compact: SPACING.md,
    normal: SPACING.xl,
    spacious: SPACING['2xl'],
  }

  const gapMap = {
    compact: SPACING.sm,
    normal: SPACING.md,
    spacious: SPACING.lg,
  }

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        className,
      )}
      style={{
        padding: paddingMap[size],
      }}
    >
      {/* Icon */}
      {icon && (
        <div
          className="mb-4 text-5xl opacity-50"
          role="img"
          aria-hidden="true"
        >
          {icon}
        </div>
      )}

      {/* Title */}
      <h3 className="text-lg font-semibold text-foreground mb-2">
        {title}
      </h3>

      {/* Description */}
      {description && (
        <p className="text-sm text-muted-foreground max-w-sm">
          {description}
        </p>
      )}

      {/* Action */}
      {action && (
        <div
          className="mt-6"
          style={{
            marginTop: gapMap[size],
          }}
        >
          {action}
        </div>
      )}
    </div>
  )
}

/**
 * Compact variant for sidebars and small containers
 */
export function EmptyStateCompact({
  icon,
  title,
  action,
}: Omit<EmptyStateProps, 'size'>) {
  return (
    <EmptyState
      icon={icon}
      title={title}
      action={action}
      size="compact"
      className="py-6"
    />
  )
}

/**
 * With border variant for card-like presentation
 */
export function EmptyStateCard({
  icon,
  title,
  description,
  action,
}: Omit<EmptyStateProps, 'size'>) {
  return (
    <div className="rounded-lg border-2 border-dashed border-muted-foreground/20 bg-muted/5">
      <EmptyState
        icon={icon}
        title={title}
        description={description}
        action={action}
        size="spacious"
      />
    </div>
  )
}
