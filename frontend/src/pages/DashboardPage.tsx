import { useEffect } from 'react'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { WidgetGrid } from '@/components/widgets/WidgetGrid'

export function DashboardPage() {
  const addRecentItem = useFavoritesStore((s) => s.addRecentItem)

  useEffect(() => {
    addRecentItem('dashboard', 'project', 'Dashboard')
  }, [addRecentItem])

  return (
    <div className="p-6">
      <WidgetGrid />
    </div>
  )
}
