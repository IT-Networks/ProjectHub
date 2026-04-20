import { useEffect, useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { LayoutGrid, Layers, Kanban, Calendar, Mail, Zap, Settings, Clock, Star } from 'lucide-react'
import { useProjectStore } from '@/stores/projectStore'
import { useTodoQueueStore } from '@/stores/todoQueueStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { label: 'Dashboard', path: '/', icon: LayoutGrid },
  { label: 'Projekte', path: '/projekte', icon: Layers },
  { label: 'Kanban', path: '/kanban', icon: Kanban },
  { label: 'Timeline', path: '/timeline', icon: Calendar },
  { label: 'Inbox', path: '/inbox', icon: Mail },
  { label: 'Todo-Queue', path: '/queue', icon: Zap, badgeKey: 'queue' as const },
]

export function Sidebar() {
  const projects = useProjectStore((s) => s.projects)
  const queueStats = useTodoQueueStore((s) => s.stats)
  const fetchStats = useTodoQueueStore((s) => s.fetchStats)
  const rawFavorites = useFavoritesStore((s) => s.favorites)
  const rawRecentItems = useFavoritesStore((s) => s.recentItems)

  const favorites = useMemo(() => rawFavorites.sort((a, b) => a.order - b.order), [rawFavorites])
  const recentItems = useMemo(() => {
    const sorted = [...rawRecentItems].sort((a, b) => {
      const aTime = a.accessedAt instanceof Date ? a.accessedAt.getTime() : new Date(a.accessedAt as any).getTime()
      const bTime = b.accessedAt instanceof Date ? b.accessedAt.getTime() : new Date(b.accessedAt as any).getTime()
      return bTime - aTime
    })
    return sorted
  }, [rawRecentItems])

  useEffect(() => { fetchStats() }, [fetchStats])

  return (
    <aside className="flex h-screen w-60 flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <span className="text-lg font-semibold tracking-tight">ProjectHub</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 overflow-y-auto px-2 py-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                isActive
                  ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                  : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground'
              )
            }
          >
            <item.icon className="w-5 h-5" />
            <span className="flex-1">{item.label}</span>
            {item.badgeKey === 'queue' && queueStats.pending > 0 && (
              <span className="rounded-full bg-primary px-1.5 py-0.5 text-xs font-medium text-primary-foreground">
                {queueStats.pending}
              </span>
            )}
          </NavLink>
        ))}

        {/* Separator */}
        <div className="my-3 h-px bg-border" />

        {/* Favorites */}
        {favorites.length > 0 && (
          <>
            <div className="flex items-center gap-2 px-3 pb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Star className="w-3 h-3" />
              Favoriten
            </div>
            {favorites.map((fav) => {
              const project = projects.find((p) => p.id === fav.id)
              if (!project) return null
              return (
                <NavLink
                  key={fav.id}
                  to={`/projekte/${fav.id}`}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 rounded-md px-3 py-1.5 text-sm transition-colors',
                      isActive
                        ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                        : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50'
                    )
                  }
                >
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: project.color }}
                  />
                  <span className="truncate text-yellow-500">⭐</span>
                  <span className="truncate flex-1">{project.name}</span>
                </NavLink>
              )
            })}
            <div className="my-2 h-px bg-border" />
          </>
        )}

        {/* Recent Items */}
        {recentItems.length > 0 && (
          <>
            <div className="flex items-center gap-2 px-3 pb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Clock className="w-3 h-3" />
              Zuletzt angesehen
            </div>
            {recentItems.slice(0, 5).map((item) => {
              const project = projects.find((p) => p.id === item.id)
              if (!project) return null

              const getTimeLabel = () => {
                const minutesAgo = Math.floor(
                  (Date.now() - new Date(item.accessedAt).getTime()) / 60000
                )
                return minutesAgo < 1
                  ? 'gerade eben'
                  : minutesAgo < 60
                    ? `vor ${minutesAgo}m`
                    : minutesAgo < 1440
                      ? `vor ${Math.floor(minutesAgo / 60)}h`
                      : `vor ${Math.floor(minutesAgo / 1440)}d`
              }
              const timeLabel = getTimeLabel()

              return (
                <NavLink
                  key={`recent-${item.id}`}
                  to={`/projekte/${item.id}`}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2 rounded-md px-3 py-1.5 text-xs transition-colors',
                      isActive
                        ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                        : 'text-sidebar-foreground/60 hover:bg-sidebar-accent/50'
                    )
                  }
                  title={project.name}
                >
                  <span
                    className="h-2 w-2 rounded-full flex-shrink-0"
                    style={{ backgroundColor: project.color }}
                  />
                  <span className="truncate flex-1">{project.name}</span>
                  <span className="whitespace-nowrap text-muted-foreground/60">
                    {timeLabel}
                  </span>
                </NavLink>
              )
            })}
            <div className="my-2 h-px bg-border" />
          </>
        )}

        {/* Projektliste */}
        <div className="px-3 pb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Alle Projekte
        </div>
        {projects.map((p) => (
          <NavLink
            key={p.id}
            to={`/projekte/${p.id}`}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-1.5 text-sm transition-colors',
                isActive
                  ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                  : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50'
              )
            }
          >
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: p.color }}
            />
            <span className="truncate">{p.name}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-border px-2 py-2">
        <NavLink
          to="/einstellungen"
          className={({ isActive }) =>
            cn(
              'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
              isActive
                ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50'
            )
          }
        >
          <Settings className="w-5 h-5" />
          Einstellungen
        </NavLink>
      </div>
    </aside>
  )
}
