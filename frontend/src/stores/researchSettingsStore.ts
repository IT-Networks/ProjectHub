import { create } from 'zustand'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/stores/toastStore'
import type {
  ResearchProvider,
  ResearchProviderHealth,
  ResearchSettings,
  ResearchSettingsUpdate,
} from '@/lib/types'

/**
 * Research-Settings-Store — per-Projekt Sourcen-Konfiguration.
 *
 * Hält die letzten Settings + Provider-Liste + Health-Snapshot pro
 * Projekt. UI: `SourcesPanel.tsx` rendert daraus die "Wissens-Quellen"-
 * Tab im Projekt-Settings.
 */
interface ResearchSettingsStore {
  settingsByProject: Record<string, ResearchSettings | undefined>
  providersByProject: Record<string, ResearchProvider[] | undefined>
  healthByProject: Record<string, ResearchProviderHealth[] | undefined>
  loadingByProject: Record<string, boolean | undefined>

  fetchSettings: (projectId: string) => Promise<void>
  fetchProviders: (projectId: string) => Promise<void>
  fetchHealth: (projectId: string) => Promise<void>
  updateSettings: (
    projectId: string,
    patch: ResearchSettingsUpdate,
  ) => Promise<ResearchSettings | null>
  toggleProvider: (projectId: string, key: string) => Promise<void>
}

const PREFIX = '/research'

export const useResearchSettingsStore = create<ResearchSettingsStore>(
  (set, get) => ({
    settingsByProject: {},
    providersByProject: {},
    healthByProject: {},
    loadingByProject: {},

    fetchSettings: async (projectId) => {
      try {
        const settings = await api.get<ResearchSettings>(
          `${PREFIX}/${projectId}/settings`,
        )
        set((s) => ({
          settingsByProject: { ...s.settingsByProject, [projectId]: settings },
        }))
      } catch (e) {
        if (e instanceof ApiError && e.status !== 404) {
          toast.error(`Settings laden fehlgeschlagen: ${e.message}`)
        }
      }
    },

    fetchProviders: async (projectId) => {
      try {
        const providers = await api.get<ResearchProvider[]>(
          `${PREFIX}/${projectId}/providers`,
        )
        set((s) => ({
          providersByProject: { ...s.providersByProject, [projectId]: providers },
        }))
      } catch (e) {
        if (e instanceof ApiError) {
          toast.error(`Provider laden fehlgeschlagen: ${e.message}`)
        }
      }
    },

    fetchHealth: async (projectId) => {
      set((s) => ({
        loadingByProject: { ...s.loadingByProject, [projectId]: true },
      }))
      try {
        const health = await api.get<ResearchProviderHealth[]>(
          `${PREFIX}/${projectId}/providers/health?refresh=true`,
        )
        set((s) => ({
          healthByProject: { ...s.healthByProject, [projectId]: health },
        }))
      } catch (e) {
        if (e instanceof ApiError) {
          toast.error(`Health-Check fehlgeschlagen: ${e.message}`)
        }
      } finally {
        set((s) => ({
          loadingByProject: { ...s.loadingByProject, [projectId]: false },
        }))
      }
    },

    updateSettings: async (projectId, patch) => {
      try {
        const updated = await api.put<ResearchSettings>(
          `${PREFIX}/${projectId}/settings`,
          patch,
        )
        set((s) => ({
          settingsByProject: { ...s.settingsByProject, [projectId]: updated },
        }))
        // Re-fetch providers because their `enabled` flag may have shifted.
        await get().fetchProviders(projectId)
        return updated
      } catch (e) {
        if (e instanceof ApiError) {
          toast.error(`Settings speichern fehlgeschlagen: ${e.message}`)
        }
        return null
      }
    },

    toggleProvider: async (projectId, key) => {
      const current =
        get().settingsByProject[projectId]?.enabled_providers ??
        get()
          .providersByProject[projectId]?.filter((p) => p.enabled)
          .map((p) => p.key) ??
        []
      const next = current.includes(key)
        ? current.filter((k) => k !== key)
        : [...current, key]
      await get().updateSettings(projectId, { enabled_providers: next })
    },
  }),
)
