import { useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { useProjectStore } from '@/stores/projectStore'
import { useTodoQueueStore } from '@/stores/todoQueueStore'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { label: 'Dashboard', path: '/', icon: '◫' },
  { label: 'Projekte', path: '/projekte', icon: '▦' },
  { label: 'Kanban', path: '/kanban', icon: '☰' },
  { label: 'Timeline', path: '/timeline', icon: '▬' },
  { label: 'Inbox', path: '/inbox', icon: '✉' },
  { label: 'Todo-Queue', path: '/queue', icon: '⚡', badgeKey: 'queue' as const },
]

export function Sidebar() {
  const projects = useProjectStore((s) => s.projects)
  const queueStats = useTodoQueueStore((s) => s.stats)
  const fetchStats = useTodoQueueStore((s) => s.fetchStats)

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
            <span className="w-5 text-center">{item.icon}</span>
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

        {/* Projektliste */}
        <div className="px-3 pb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Projekte
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
          <span className="w-5 text-center">⚙</span>
          Einstellungen
        </NavLink>
      </div>
    </aside>
  )
}
