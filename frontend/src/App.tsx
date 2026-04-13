import { BrowserRouter, Routes, Route, Outlet } from 'react-router-dom'
import { useEffect } from 'react'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar } from '@/components/layout/TopBar'
import { DashboardPage } from '@/pages/DashboardPage'
import { ProjectListPage } from '@/pages/ProjectListPage'
import { ProjectPage } from '@/pages/ProjectPage'
import { KanbanPage } from '@/pages/KanbanPage'
import { InboxPage } from '@/pages/InboxPage'
import { TodoQueuePage } from '@/pages/TodoQueuePage'
import { SettingsPage } from '@/pages/SettingsPage'
import { TimelinePage } from '@/pages/TimelinePage'
import { useProjectStore } from '@/stores/projectStore'
import { useSSEConnection } from '@/hooks/useSSE'
import { useOfflineMonitor, useIsOffline } from '@/hooks/useOffline'
import { useKeyboardShortcuts } from '@/hooks/useKeyboard'
import { CommandPalette } from '@/components/layout/CommandPalette'
import { useThemeStore } from '@/stores/themeStore'

function AppLayout() {
  const fetchProjects = useProjectStore((s) => s.fetchProjects)
  const isOffline = useIsOffline()
  const theme = useThemeStore((s) => s.theme)

  // Initialize SSE, offline monitoring, and keyboard shortcuts
  useSSEConnection()
  useOfflineMonitor()
  useKeyboardShortcuts()

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  return (
    <div className={`${theme} flex h-screen bg-background text-foreground`}>
      <CommandPalette />
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        {isOffline && (
          <div className="border-b border-yellow-500/30 bg-yellow-500/10 px-6 py-2 text-sm text-yellow-400">
            Offline — AI-Assist nicht erreichbar. Lokale Daten verfügbar.
          </div>
        )}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/projekte" element={<ProjectListPage />} />
          <Route path="/projekte/:id" element={<ProjectPage />} />
          <Route path="/kanban" element={<KanbanPage />} />
          <Route path="/timeline" element={<TimelinePage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/queue" element={<TodoQueuePage />} />
          <Route path="/einstellungen" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
