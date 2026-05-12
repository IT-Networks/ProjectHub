import { create } from 'zustand'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/stores/toastStore'

export interface SyncSource {
  id: string
  source_type: string
  display_name: string
  last_synced_at: string | null
  last_sync_status: 'idle' | 'in_progress' | 'ok' | 'error'
  last_error_msg: string | null
  sync_enabled: boolean
}

export interface SyncRun {
  id: string
  started_at: string
  finished_at: string | null
  trigger: 'manual' | 'auto_open' | 'periodic' | 'sse_event'
  status: 'running' | 'ok' | 'partial' | 'error'
  sources_synced: number
  sources_failed: number
  changes_detected: number
  error_summary: string | null
}

export interface SyncStatus {
  running: boolean
  last_run: SyncRun | null
  pending_changes: number
  sources: SyncSource[]
}

interface TriggerResponse {
  run_id: string | null
  started: boolean
  reason: 'started' | 'cooldown' | 'already_running' | 'no_sources'
}

export interface SourceChange {
  id: string
  source_type: 'pr' | 'build' | 'commit' | 'commit_batch' | 'codebase_baseline' | 'jira' | 'jira_comment' | 'pr_comment'
  external_ref: string
  title: string
  detected_at: string
  analysis_status: 'pending' | 'analyzing' | 'analyzed' | 'accepted' | 'dismissed' | 'error'
  analysis: {
    relevance: 'core' | 'related' | 'irrelevant'
    reason: string
    summary: string
    category: string
    tags: string[]
    title: string
    confidence: number
  } | null
  auto_accepted: boolean
  knowledge_item_id: string | null
}

interface ProjectSyncStore {
  statusByProject: Record<string, SyncStatus | undefined>
  changesByProject: Record<string, SourceChange[] | undefined>

  fetchStatus: (projectId: string) => Promise<SyncStatus | null>
  fetchChanges: (projectId: string, status?: string) => Promise<SourceChange[]>
  triggerSync: (projectId: string, trigger?: 'manual' | 'auto_open' | 'periodic', force?: boolean) => Promise<TriggerResponse | null>
  analyzePending: (projectId: string) => Promise<void>
  acceptChange: (projectId: string, changeId: string) => Promise<boolean>
  dismissChange: (projectId: string, changeId: string) => Promise<boolean>
  // Called by SSE listener; refetches status so UI reflects completed run
  onSyncComplete: (projectId: string) => Promise<void>
}

export const useProjectSyncStore = create<ProjectSyncStore>((set, get) => ({
  statusByProject: {},
  changesByProject: {},

  fetchStatus: async (projectId) => {
    try {
      const status = await api.get<SyncStatus>(`/projects/${projectId}/sync/status`)
      set((s) => ({ statusByProject: { ...s.statusByProject, [projectId]: status } }))
      return status
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // project gone — silently clear
        set((s) => {
          const next = { ...s.statusByProject }
          delete next[projectId]
          return { statusByProject: next }
        })
      }
      return null
    }
  },

  fetchChanges: async (projectId, status = 'pending') => {
    try {
      const list = await api.get<SourceChange[]>(`/projects/${projectId}/sync/changes?status=${status}&limit=100`)
      set((s) => ({ changesByProject: { ...s.changesByProject, [projectId]: list } }))
      return list
    } catch {
      return []
    }
  },

  analyzePending: async (projectId) => {
    try {
      await api.post(`/projects/${projectId}/sync/analyze`, {})
      await get().fetchStatus(projectId)
      await get().fetchChanges(projectId)
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Analyse fehlgeschlagen', { description: msg })
    }
  },

  acceptChange: async (projectId, changeId) => {
    try {
      await api.post(`/projects/${projectId}/sync/changes/${changeId}/accept`, {})
      await get().fetchChanges(projectId)
      await get().fetchStatus(projectId)
      toast.success('Als Wissen übernommen')
      return true
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Übernehmen fehlgeschlagen', { description: msg })
      return false
    }
  },

  dismissChange: async (projectId, changeId) => {
    try {
      await api.post(`/projects/${projectId}/sync/changes/${changeId}/dismiss`, {})
      await get().fetchChanges(projectId)
      await get().fetchStatus(projectId)
      return true
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Verwerfen fehlgeschlagen', { description: msg })
      return false
    }
  },

  triggerSync: async (projectId, trigger = 'manual', force = false) => {
    try {
      const res = await api.post<TriggerResponse>(
        `/projects/${projectId}/sync`,
        { trigger, force },
      )
      // Refresh status immediately so UI reflects "running"
      if (res.started) void get().fetchStatus(projectId)
      return res
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Sync-Trigger fehlgeschlagen', { description: msg })
      return null
    }
  },

  onSyncComplete: async (projectId) => {
    const status = await get().fetchStatus(projectId)
    await get().fetchChanges(projectId)
    if (!status?.last_run) return
    const run = status.last_run
    if (run.status === 'ok' && run.changes_detected > 0) {
      toast.success(`${run.changes_detected} neue Änderung(en) erkannt`, {
        description: 'Prüfe den Tab „Änderungen" im Projekt',
        duration: 6000,
      })
    } else if (run.status === 'error') {
      toast.error('Sync fehlgeschlagen', {
        description: run.error_summary ?? 'Siehe Logs',
      })
    } else if (run.status === 'partial') {
      toast.warning('Sync teilweise erfolgreich', {
        description: `${run.sources_failed} Quelle(n) fehlgeschlagen, ${run.changes_detected} Änderung(en) erkannt`,
      })
    }
    // status === 'ok' && changes_detected === 0 → silent (no noise)
  },
}))
