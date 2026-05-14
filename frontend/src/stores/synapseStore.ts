import { create } from 'zustand'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/stores/toastStore'
import type {
  Synapse,
  SynapseDetail,
  SynapseRun,
  ReviewQueueItem,
  SynapseGenerateResponse,
  AskResponse,
  HumanVerdict,
} from '@/lib/types'

/**
 * Synapsen-Store — Wissens-Synthese & Validierung.
 *
 * Per-Projekt-Records wie der projectSyncStore. Die Live-Fortschritts-
 * anzeige eines laufenden Generierungs-Laufs kommt über SSE
 * (``synapse_progress`` / ``synapse_complete``) und wird von
 * SynapseGenerateBar verarbeitet — dieser Store hält die persistenten
 * Daten (Synapsen, letzter Lauf, Review-Queue, Detail-Cache).
 */
interface SynapseStore {
  synapsesByProject: Record<string, Synapse[] | undefined>
  latestRunByProject: Record<string, SynapseRun | null | undefined>
  reviewQueueByProject: Record<string, ReviewQueueItem[] | undefined>
  detailById: Record<string, SynapseDetail | undefined>
  loadingByProject: Record<string, boolean | undefined>

  fetchSynapses: (projectId: string) => Promise<void>
  fetchLatestRun: (projectId: string) => Promise<SynapseRun | null>
  fetchReviewQueue: (projectId: string) => Promise<void>
  fetchDetail: (projectId: string, synapseId: string) => Promise<void>
  generate: (projectId: string) => Promise<SynapseGenerateResponse | null>
  deleteSynapse: (projectId: string, synapseId: string) => Promise<void>
  reviewSynapse: (
    projectId: string,
    synapseId: string,
    verdict: HumanVerdict,
  ) => Promise<void>
  ask: (projectId: string, question: string) => Promise<AskResponse | null>
  /** Called by the SSE `synapse_complete` listener — refresh everything. */
  onComplete: (projectId: string) => Promise<void>
}

const PREFIX = '/synapse'

export const useSynapseStore = create<SynapseStore>((set, get) => ({
  synapsesByProject: {},
  latestRunByProject: {},
  reviewQueueByProject: {},
  detailById: {},
  loadingByProject: {},

  fetchSynapses: async (projectId) => {
    set((s) => ({ loadingByProject: { ...s.loadingByProject, [projectId]: true } }))
    try {
      const synapses = await api.get<Synapse[]>(`${PREFIX}/${projectId}/synapses`)
      set((s) => ({
        synapsesByProject: { ...s.synapsesByProject, [projectId]: synapses },
        loadingByProject: { ...s.loadingByProject, [projectId]: false },
      }))
    } catch {
      set((s) => ({ loadingByProject: { ...s.loadingByProject, [projectId]: false } }))
    }
  },

  fetchLatestRun: async (projectId) => {
    try {
      const runs = await api.get<SynapseRun[]>(`${PREFIX}/${projectId}/runs?limit=1`)
      const latest = runs[0] ?? null
      set((s) => ({ latestRunByProject: { ...s.latestRunByProject, [projectId]: latest } }))
      return latest
    } catch {
      return null
    }
  },

  fetchReviewQueue: async (projectId) => {
    try {
      const queue = await api.get<ReviewQueueItem[]>(
        `${PREFIX}/${projectId}/review-queue`,
      )
      set((s) => ({
        reviewQueueByProject: { ...s.reviewQueueByProject, [projectId]: queue },
      }))
    } catch {
      /* keep stale data on failure */
    }
  },

  fetchDetail: async (projectId, synapseId) => {
    try {
      const detail = await api.get<SynapseDetail>(
        `${PREFIX}/${projectId}/synapses/${synapseId}`,
      )
      set((s) => ({ detailById: { ...s.detailById, [synapseId]: detail } }))
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Synapse konnte nicht geladen werden', { description: msg })
    }
  },

  generate: async (projectId) => {
    try {
      const res = await api.post<SynapseGenerateResponse>(
        `${PREFIX}/${projectId}/generate`,
      )
      if (res.started) {
        void get().fetchLatestRun(projectId)
      } else if (res.reason === 'already_running') {
        toast.info('Eine Synthese läuft bereits für dieses Projekt')
      }
      return res
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Synthese konnte nicht gestartet werden', { description: msg })
      return null
    }
  },

  deleteSynapse: async (projectId, synapseId) => {
    try {
      await api.del(`${PREFIX}/${projectId}/synapses/${synapseId}`)
      set((s) => ({
        synapsesByProject: {
          ...s.synapsesByProject,
          [projectId]: (s.synapsesByProject[projectId] ?? []).filter(
            (syn) => syn.id !== synapseId,
          ),
        },
      }))
      void get().fetchReviewQueue(projectId)
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Löschen fehlgeschlagen', { description: msg })
    }
  },

  reviewSynapse: async (projectId, synapseId, verdict) => {
    try {
      const updated = await api.post<Synapse>(
        `${PREFIX}/${projectId}/synapses/${synapseId}/review`,
        { verdict },
      )
      set((s) => ({
        synapsesByProject: {
          ...s.synapsesByProject,
          [projectId]: (s.synapsesByProject[projectId] ?? []).map((syn) =>
            syn.id === synapseId ? updated : syn,
          ),
        },
      }))
      await get().fetchReviewQueue(projectId)
      toast.success(
        verdict === 'accepted'
          ? 'Synapse übernommen'
          : verdict === 'rejected'
            ? 'Synapse verworfen'
            : 'Review abgeschlossen',
      )
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Review fehlgeschlagen', { description: msg })
    }
  },

  ask: async (projectId, question) => {
    try {
      return await api.post<AskResponse>(`${PREFIX}/${projectId}/ask`, { question })
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message
      toast.error('Frage fehlgeschlagen', { description: msg })
      return null
    }
  },

  onComplete: async (projectId) => {
    const run = await get().fetchLatestRun(projectId)
    await get().fetchSynapses(projectId)
    await get().fetchReviewQueue(projectId)
    if (!run) return
    if (run.status === 'ok') {
      toast.success(
        `Synthese fertig: ${run.synapse_count} Synapse(n)`,
        {
          description:
            `${run.validated_count} validiert · ${run.flagged_count} ungeprüft · ` +
            `${run.review_count} im Review`,
        },
      )
    } else if (run.status === 'error') {
      toast.error('Synthese fehlgeschlagen', {
        description: run.error_summary ?? 'Siehe Logs',
      })
    }
  },
}))
