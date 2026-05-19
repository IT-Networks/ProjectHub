import { create } from 'zustand'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/stores/toastStore'
import type {
  ResearchDepth,
  ResearchFinding,
  ResearchRunDetail,
  ResearchRunSummary,
  ResearchStartRunRequest,
  ResearchStartRunResponse,
} from '@/lib/types'

/**
 * Research-Store — Auto-Mode run lifecycle.
 *
 * Hält für jedes Projekt den aktiv laufenden Run + die letzten Runs.
 * Live-Updates kommen über SSE (research_progress / _finding /
 * _subquery_started / _budget / _lateral_planned / _complete) — die
 * Bar-Komponente subscribed direkt; dieser Store hält den persisten
 * State + die HTTP-Actions.
 */
interface ResearchStore {
  runsByProject: Record<string, ResearchRunSummary[] | undefined>
  activeRunByProject: Record<string, ResearchRunSummary | null | undefined>
  detailByRun: Record<string, ResearchRunDetail | undefined>
  // Per-run scratch state for the Auto-Bar:
  liveFindings: Record<string, ResearchFinding[]>
  livePhase: Record<string, string | undefined>
  livePressure: Record<string, string | undefined>
  liveCounts: Record<string, { findings: number; persisted: number; flagged: number; rejected: number } | undefined>

  startRun: (
    projectId: string,
    req: ResearchStartRunRequest,
  ) => Promise<ResearchStartRunResponse | null>
  fetchRuns: (projectId: string) => Promise<void>
  fetchActive: (projectId: string) => Promise<ResearchRunSummary | null>
  fetchDetail: (runId: string) => Promise<ResearchRunDetail | null>
  cancelRun: (runId: string) => Promise<void>
  acceptFinding: (
    runId: string,
    findingId: string,
    note?: string,
  ) => Promise<void>
  rejectFinding: (
    runId: string,
    findingId: string,
    note?: string,
  ) => Promise<void>

  // Live-feed handlers — called by ResearchAutoBar's SSE listeners.
  pushFinding: (runId: string, f: ResearchFinding) => void
  setLivePhase: (runId: string, phase: string) => void
  setLivePressure: (runId: string, level: string) => void
  resetLive: (runId: string) => void
}

const PREFIX = '/research'

export const useResearchStore = create<ResearchStore>((set, get) => ({
  runsByProject: {},
  activeRunByProject: {},
  detailByRun: {},
  liveFindings: {},
  livePhase: {},
  livePressure: {},
  liveCounts: {},

  startRun: async (projectId, req) => {
    try {
      const res = await api.post<ResearchStartRunResponse>(
        `${PREFIX}/${projectId}/runs`,
        req,
      )
      get().resetLive(res.run_id)
      // Refresh active-run pointer.
      await get().fetchActive(projectId)
      return res
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 409) {
          toast.error('Bereits ein Recherche-Lauf in diesem Projekt aktiv.')
        } else {
          toast.error(`Recherche starten fehlgeschlagen: ${e.message}`)
        }
      }
      return null
    }
  },

  fetchRuns: async (projectId) => {
    try {
      const runs = await api.get<ResearchRunSummary[]>(
        `${PREFIX}/${projectId}/runs?limit=20`,
      )
      set((s) => ({
        runsByProject: { ...s.runsByProject, [projectId]: runs },
      }))
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(`Recherche-Liste laden fehlgeschlagen: ${e.message}`)
      }
    }
  },

  fetchActive: async (projectId) => {
    try {
      const runs = await api.get<ResearchRunSummary[]>(
        `${PREFIX}/${projectId}/runs?limit=1&status=running`,
      )
      const active = runs[0] ?? null
      set((s) => ({
        activeRunByProject: { ...s.activeRunByProject, [projectId]: active },
      }))
      return active
    } catch {
      return null
    }
  },

  fetchDetail: async (runId) => {
    try {
      const detail = await api.get<ResearchRunDetail>(
        `${PREFIX}/runs/${runId}`,
      )
      set((s) => ({
        detailByRun: { ...s.detailByRun, [runId]: detail },
      }))
      return detail
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(`Detail laden fehlgeschlagen: ${e.message}`)
      }
      return null
    }
  },

  cancelRun: async (runId) => {
    try {
      await api.post<{ cancelled: boolean; reason?: string }>(
        `${PREFIX}/runs/${runId}/cancel`,
      )
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(`Cancel fehlgeschlagen: ${e.message}`)
      }
    }
  },

  acceptFinding: async (runId, findingId, note) => {
    try {
      await api.post(
        `${PREFIX}/runs/${runId}/findings/${findingId}/accept`,
        { note },
      )
      // Refresh detail so the UI status updates.
      await get().fetchDetail(runId)
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(`Accept fehlgeschlagen: ${e.message}`)
      }
    }
  },

  rejectFinding: async (runId, findingId, note) => {
    try {
      await api.post(
        `${PREFIX}/runs/${runId}/findings/${findingId}/reject`,
        { note },
      )
      await get().fetchDetail(runId)
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(`Reject fehlgeschlagen: ${e.message}`)
      }
    }
  },

  pushFinding: (runId, f) => {
    set((s) => {
      const prev = s.liveFindings[runId] ?? []
      const counts = s.liveCounts[runId] ?? { findings: 0, persisted: 0, flagged: 0, rejected: 0 }
      return {
        liveFindings: { ...s.liveFindings, [runId]: [...prev, f] },
        liveCounts: {
          ...s.liveCounts,
          [runId]: {
            findings: counts.findings + 1,
            persisted: counts.persisted + (f.status === 'persisted' ? 1 : 0),
            flagged: counts.flagged + (f.status === 'flagged' ? 1 : 0),
            rejected: counts.rejected + (f.status === 'rejected' ? 1 : 0),
          },
        },
      }
    })
  },

  setLivePhase: (runId, phase) => {
    set((s) => ({
      livePhase: { ...s.livePhase, [runId]: phase },
    }))
  },

  setLivePressure: (runId, level) => {
    set((s) => ({
      livePressure: { ...s.livePressure, [runId]: level },
    }))
  },

  resetLive: (runId) => {
    set((s) => ({
      liveFindings: { ...s.liveFindings, [runId]: [] },
      livePhase: { ...s.livePhase, [runId]: 'planning' },
      livePressure: { ...s.livePressure, [runId]: 'ok' },
      liveCounts: { ...s.liveCounts, [runId]: { findings: 0, persisted: 0, flagged: 0, rejected: 0 } },
    }))
  },
}))
