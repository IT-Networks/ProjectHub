import { type User } from '@/lib/types'
import { initials, avatarHue, CURRENT_USER } from '@/lib/currentUser'
import { cn } from '@/lib/utils'

type Size = 'xs' | 'sm' | 'md'

const SIZE_CLASS: Record<Size, string> = {
  xs: 'h-5 w-5 text-[10px]',
  sm: 'h-6 w-6 text-[11px]',
  md: 'h-8 w-8 text-sm',
}

interface Props {
  user?: User | null
  userId?: string | null
  size?: Size
  className?: string
}

export function AssigneeAvatar({ user, userId, size = 'xs', className }: Props) {
  const resolved = user ?? (userId === CURRENT_USER.id ? CURRENT_USER : null)
  if (!resolved) return null

  if (resolved.avatar_url) {
    return (
      <img
        src={resolved.avatar_url}
        alt={resolved.name}
        className={cn('rounded-full object-cover', SIZE_CLASS[size], className)}
      />
    )
  }

  const hue = avatarHue(resolved)
  return (
    <span
      aria-label={resolved.name}
      title={resolved.name}
      className={cn(
        'inline-flex items-center justify-center rounded-full font-semibold text-white ring-1 ring-black/5 dark:ring-white/10',
        SIZE_CLASS[size],
        className,
      )}
      style={{ backgroundColor: `oklch(0.55 0.16 ${hue})` }}
    >
      {initials(resolved.name)}
    </span>
  )
}
