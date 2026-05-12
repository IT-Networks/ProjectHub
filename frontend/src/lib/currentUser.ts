import type { User } from './types'

export const CURRENT_USER: User = {
  id: 'me',
  name: 'Me',
  email: '',
  avatar_url: null,
  color_hue: 262,
}

export function isCurrentUser(userId: string | null | undefined): boolean {
  return userId === CURRENT_USER.id
}

export function resolveUser(
  userId: string | null | undefined,
  directory?: readonly User[],
): User | null {
  if (!userId) return null
  if (userId === CURRENT_USER.id) return CURRENT_USER
  const hit = directory?.find((u) => u.id === userId)
  return hit ?? null
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export function avatarHue(user: Pick<User, 'id' | 'color_hue'>): number {
  if (user.color_hue !== undefined && user.color_hue !== null) return user.color_hue
  let hash = 0
  for (let i = 0; i < user.id.length; i++) {
    hash = (hash * 31 + user.id.charCodeAt(i)) | 0
  }
  return Math.abs(hash) % 360
}
