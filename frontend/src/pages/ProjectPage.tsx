import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useViewTransitionNavigate } from '@/hooks/useViewTransition'
import { useProjectStore } from '@/stores/projectStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { useProjectSyncStore } from '@/stores/projectSyncStore'
import { useSSEEvent } from '@/hooks/useSSE'
import { useToast } from '@/components/shared/Toast'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { STATUS_LABELS, SOURCE_TYPE_LABELS } from '@/lib/types'
import type { SourceType, DataSourceLinkCreate } from '@/lib/types'
import { TodoList } from '@/components/shared/TodoList'
import { NoteList } from '@/components/shared/NoteList'
import { LinkedMessageList } from '@/components/shared/LinkedMessageList'
import { ResearchList } from '@/components/shared/ResearchList'
import { PRReviewPanel } from '@/components/shared/PRReviewPanel'
import { ProjectChat } from '@/components/chat/ProjectChat'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { KnowledgeTab } from '@/components/knowledge/KnowledgeTab'
import { FormField } from '@/components/shared/FormField'
import { FavoriteButton } from '@/components/shared/FavoriteButton'
import { SyncIndicator } from '@/components/shared/SyncIndicator'
import { ChangesReviewPanel } from '@/components/sync/ChangesReviewPanel'

const SOURCE_FIELDS: Record<SourceType, { label: string; fields: { key: string; label: string; placeholder: string }[] }> = {
  jenkins_job: {
    label: 'Jenkins Job',
    fields: [
      { key: 'path_name', label: 'Job-Pfad', placeholder: 'z.B. OSPE' },
      { key: 'job_name', label: 'Job-Name', placeholder: 'z.B. my-build' },
    ],
  },
  github_repo: {
    label: 'GitHub Repo',
    fields: [
      { key: 'owner', label: 'Owner/Org', placeholder: 'z.B. my-org' },
      { key: 'repo', label: 'Repository', placeholder: 'z.B. my-repo' },
    ],
  },
  git_repo: {
    label: 'Git Repository',
    fields: [{ key: 'path', label: 'Pfad', placeholder: 'z.B. /home/user/repos/my-repo' }],
  },
  jira_project: {
    label: 'Jira Projekt',
    fields: [{ key: 'project_key', label: 'Projekt-Key', placeholder: 'z.B. PROJ' }],
  },
  confluence_space: {
    label: 'Confluence Space',
    fields: [{ key: 'space_key', label: 'Space-Key', placeholder: 'z.B. DEV' }],
  },
  email_folder: {
    label: 'Email-Ordner',
    fields: [{ key: 'folder', label: 'Ordner', placeholder: 'z.B. Inbox/Projekt-X' }],
  },
  webex_room: {
    label: 'Webex Raum',
    fields: [
      { key: 'room_id', label: 'Raum-ID', placeholder: 'Webex Room ID' },
      { key: 'room_title', label: 'Raum-Name', placeholder: 'z.B. Team X' },
    ],
  },
}

export function ProjectPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useViewTransitionNavigate()
  const { currentProject, loading, fetchProject, deleteProject, addSource, removeSource } = useProjectStore()
  const { success, error } = useToast()
  const addRecentItem = useFavoritesStore((s) => s.addRecentItem)

  const [chatOpen, setChatOpen] = useState(false)
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const [sourceType, setSourceType] = useState<SourceType>('jenkins_job')
  const [sourceConfig, setSourceConfig] = useState<Record<string, string>>({})
  const [sourceDisplayName, setSourceDisplayName] = useState('')
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [removeSourceId, setRemoveSourceId] = useState<string | null>(null)
  const [submittingSource, setSubmittingSource] = useState(false)
  const pendingRemoveRef = useRef<{ timer: number; cancelled: boolean } | null>(null)

  useEffect(() => {
    if (id) {
      fetchProject(id)
    }
  }, [id, fetchProject])

  useEffect(() => {
    if (currentProject?.name) {
      addRecentItem(currentProject.id, 'project', currentProject.name)
    }
  }, [currentProject?.id, currentProject?.name, addRecentItem])

  // Background sync: fire-and-forget on project open; server enforces 30-min cooldown
  const triggerSync = useProjectSyncStore((s) => s.triggerSync)
  const fetchSyncStatus = useProjectSyncStore((s) => s.fetchStatus)
  const onSyncComplete = useProjectSyncStore((s) => s.onSyncComplete)
  const syncStatus = useProjectSyncStore((s) => (id ? s.statusByProject[id] : undefined))

  useEffect(() => {
    if (!id) return
    void fetchSyncStatus(id)
    void triggerSync(id, 'auto_open')
  }, [id, fetchSyncStatus, triggerSync])

  useSSEEvent('sync_complete', (payload) => {
    const data = payload as { project_id?: string }
    if (id && data.project_id === id) void onSyncComplete(id)
  })

  if (loading && !currentProject) {
    return <div className="p-6 text-muted-foreground">Laden...</div>
  }

  if (!currentProject) {
    return <div className="p-6 text-muted-foreground">Projekt nicht gefunden</div>
  }

  const p = currentProject

  const handleAddSource = async () => {
    try {
      setSubmittingSource(true)
      const data: DataSourceLinkCreate = {
        source_type: sourceType,
        source_config: sourceConfig,
        display_name: sourceDisplayName,
      }
      await addSource(p.id, data)
      success('Datenquelle erfolgreich verknüpft!')
      setSourceDialogOpen(false)
      setSourceConfig({})
      setSourceDisplayName('')
    } catch (err) {
      error(`Fehler: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
    } finally {
      setSubmittingSource(false)
    }
  }

  const handleDelete = async () => {
    const projectName = p.name
    setDeleteConfirmOpen(false)

    // Show undo toast
    success(`Projekt "${projectName}" gelöscht`, {
      action: {
        label: 'Rückgängig',
        onClick: () => {
          // User clicked undo - we just close the toast
          // The project is still there since we haven't actually deleted it yet
        },
      },
      duration: 5000,
    })

    // Actually delete after delay
    setTimeout(async () => {
      try {
        await deleteProject(p.id)
        navigate('/projekte')
      } catch (err) {
        error(`Fehler beim Löschen: ${err instanceof Error ? err.message : 'Unbekannter Fehler'}`)
      }
    }, 5000)
  }

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between">
        <div className="flex items-center gap-4 flex-1">
          <span className="h-4 w-4 rounded-full" style={{ backgroundColor: p.color }} />
          <div className="flex-1">
            <h2 className="text-xl font-semibold">{p.name}</h2>
            {p.description && (
              <p className="mt-1 text-sm text-muted-foreground">{p.description}</p>
            )}
          </div>
          <FavoriteButton
            id={p.id}
            type="project"
            title={p.name}
            size="sm"
          />
          <Badge variant="secondary">{STATUS_LABELS[p.status]}</Badge>
          {p.tags.map((tag) => (
            <Badge key={tag} variant="outline">{tag}</Badge>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <SyncIndicator
            projectId={p.id}
            running={syncStatus?.running ?? false}
            lastRun={syncStatus?.last_run ?? null}
            pending={syncStatus?.pending_changes ?? 0}
            onTrigger={() => triggerSync(p.id, 'manual', true)}
          />
          <Button variant="outline" size="sm" onClick={() => setChatOpen(true)}>
            Chat
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSourceDialogOpen(true)}>
            + Quelle
          </Button>
          <Button variant="destructive" size="sm" onClick={() => setDeleteConfirmOpen(true)}>
            Löschen
          </Button>
        </div>
      </div>

      {/* Counts */}
      <div className="mb-6 grid grid-cols-4 gap-4">
        {[
          { label: 'Offene Todos', value: p.counts.todos_open },
          { label: 'Erledigte Todos', value: p.counts.todos_done },
          { label: 'Notizen', value: p.counts.notes },
          { label: 'Recherchen', value: p.counts.research },
        ].map((stat) => (
          <Card key={stat.label} className="p-4 text-center">
            <div className="text-2xl font-bold">{stat.value}</div>
            <div className="text-xs text-muted-foreground">{stat.label}</div>
          </Card>
        ))}
      </div>

      {/* Tabs */}
      <Tabs defaultValue="uebersicht">
        <TabsList>
          <TabsTrigger value="uebersicht">Übersicht</TabsTrigger>
          <TabsTrigger value="todos" className="gap-1.5">
            Todos
            {p.counts.todos_open > 0 && (
              <span className="rounded-full bg-muted px-1.5 py-0 text-[10px] tabular-nums">{p.counts.todos_open}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="notizen" className="gap-1.5">
            Notizen
            {p.counts.notes > 0 && (
              <span className="rounded-full bg-muted px-1.5 py-0 text-[10px] tabular-nums">{p.counts.notes}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="nachrichten">Nachrichten</TabsTrigger>
          <TabsTrigger value="wissen">Wissen</TabsTrigger>
          <TabsTrigger value="recherche" className="gap-1.5">
            Recherche
            {p.counts.research > 0 && (
              <span className="rounded-full bg-muted px-1.5 py-0 text-[10px] tabular-nums">{p.counts.research}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="builds">Builds / PRs</TabsTrigger>
          <TabsTrigger value="aenderungen" className="gap-1.5">
            Änderungen
            {(syncStatus?.pending_changes ?? 0) > 0 && (
              <span className="rounded-full bg-brand px-1.5 py-0 text-[10px] tabular-nums text-brand-foreground">
                {syncStatus!.pending_changes}
              </span>
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="uebersicht" className="mt-4">
          {/* Datenquellen */}
          <h3 className="mb-3 text-sm font-medium">Verknüpfte Datenquellen</h3>
          {p.sources.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Keine Quellen verknüpft.{' '}
              <button className="text-primary underline" onClick={() => setSourceDialogOpen(true)}>
                Quelle hinzufügen
              </button>
            </p>
          ) : (
            <div className="space-y-2">
              {p.sources.map((s) => (
                <Card key={s.id} className="flex items-center justify-between p-3">
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className="text-xs">
                      {SOURCE_TYPE_LABELS[s.source_type as SourceType] || s.source_type}
                    </Badge>
                    <span className="text-sm">
                      {s.display_name || JSON.stringify(s.source_config)}
                    </span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setRemoveSourceId(s.id)}
                  >
                    Entfernen
                  </Button>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="todos" className="mt-4">
          <TodoList projectId={p.id} />
        </TabsContent>

        <TabsContent value="notizen" className="mt-4">
          <NoteList projectId={p.id} />
        </TabsContent>

        <TabsContent value="nachrichten" className="mt-4">
          <LinkedMessageList targetType="project" targetId={p.id} />
        </TabsContent>

        <TabsContent value="wissen" className="mt-4">
          <KnowledgeTab projectId={p.id} />
        </TabsContent>

        <TabsContent value="recherche" className="mt-4">
          <ResearchList projectId={p.id} />
        </TabsContent>

        <TabsContent value="builds" className="mt-4">
          <PRReviewPanel projectId={p.id} />
        </TabsContent>

        <TabsContent value="aenderungen" className="mt-4">
          <ChangesReviewPanel projectId={p.id} />
        </TabsContent>
      </Tabs>

      {/* Delete Project Confirmation */}
      <ConfirmDialog
        open={deleteConfirmOpen}
        onOpenChange={setDeleteConfirmOpen}
        title="Projekt löschen"
        description={`"${p.name}" und alle zugehörigen Todos, Notizen und Quellen werden unwiderruflich gelöscht.`}
        confirmLabel="Löschen"
        onConfirm={handleDelete}
      />

      {/* Remove Source Confirmation */}
      <ConfirmDialog
        open={!!removeSourceId}
        onOpenChange={() => setRemoveSourceId(null)}
        title="Datenquelle entfernen"
        description="Die Verknüpfung wird entfernt. Die externe Quelle bleibt unverändert."
        confirmLabel="Entfernen"
        onConfirm={() => {
          if (!removeSourceId) {
            setRemoveSourceId(null)
            return
          }
          const sourceId = removeSourceId
          const pending = { timer: 0, cancelled: false }
          pendingRemoveRef.current = pending
          success('Datenquelle entfernt', {
            action: {
              label: 'Rückgängig',
              onClick: () => {
                pending.cancelled = true
                window.clearTimeout(pending.timer)
              },
            },
            duration: 5000,
          })
          pending.timer = window.setTimeout(async () => {
            if (pending.cancelled) return
            try {
              await removeSource(p.id, sourceId)
            } catch {
              error('Fehler beim Entfernen der Quelle')
            }
          }, 5000)
          setRemoveSourceId(null)
        }}
      />

      {/* Project Chat */}
      <ProjectChat
        projectId={p.id}
        projectName={p.name}
        open={chatOpen}
        onOpenChange={setChatOpen}
      />

      {/* Add Source Dialog */}
      <Dialog open={sourceDialogOpen} onOpenChange={setSourceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Datenquelle verknüpfen</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <FormField label="Typ">
              <Select
                value={sourceType}
                onValueChange={(v) => {
                  setSourceType(v as SourceType)
                  setSourceConfig({})
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(SOURCE_FIELDS).map(([key, val]) => (
                    <SelectItem key={key} value={key}>{val.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>

            {SOURCE_FIELDS[sourceType].fields.map((field) => (
              <FormField
                key={field.key}
                label={field.label}
                success={!!sourceConfig[field.key]}
              >
                <Input
                  value={sourceConfig[field.key] || ''}
                  onChange={(e) => setSourceConfig({ ...sourceConfig, [field.key]: e.target.value })}
                  placeholder={field.placeholder}
                />
              </FormField>
            ))}

            <FormField label="Anzeigename (optional)">
              <Input
                value={sourceDisplayName}
                onChange={(e) => setSourceDisplayName(e.target.value)}
                placeholder="z.B. Haupt-Build"
              />
            </FormField>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSourceDialogOpen(false)} disabled={submittingSource}>
              Abbrechen
            </Button>
            <Button onClick={handleAddSource} disabled={submittingSource}>
              {submittingSource ? 'Verknüpfe...' : 'Verknüpfen'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
