import { Star } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useFavoritesStore } from '@/stores/favoritesStore'

interface FavoriteButtonProps {
  id: string
  type: 'project' | 'todo' | 'note'
  title: string
  icon?: string
  size?: 'sm' | 'md' | 'lg'
  variant?: 'ghost' | 'outline'
  className?: string
}

// FavoriteButton is icon-only -> map to the square `icon-*` Button sizes.
const SIZE_MAP = { sm: 'icon-sm', md: 'icon', lg: 'icon-lg' } as const

export function FavoriteButton({
  id,
  type,
  title,
  icon,
  size = 'sm',
  variant = 'ghost',
  className,
}: FavoriteButtonProps) {
  const { isFavorited, addFavorite, removeFavorite } = useFavoritesStore()
  const favorited = isFavorited(id)

  const handleToggle = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (favorited) {
      await removeFavorite(id)
    } else {
      await addFavorite(id, type, title, icon)
    }
  }

  return (
    <Button
      variant={variant}
      size={SIZE_MAP[size]}
      onClick={handleToggle}
      className={className}
      aria-label={
        favorited ? 'Aus Favoriten entfernen' : 'Zu Favoriten hinzufügen'
      }
      title={
        favorited ? 'Aus Favoriten entfernen' : 'Zu Favoriten hinzufügen'
      }
    >
      <Star
        className={`w-4 h-4 transition-colors ${
          favorited
            ? 'fill-yellow-400 text-yellow-400'
            : 'text-muted-foreground hover:text-yellow-400'
        }`}
      />
    </Button>
  )
}
