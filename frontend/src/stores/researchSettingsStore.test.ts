import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useResearchSettingsStore } from './researchSettingsStore'
import { api } from '@/lib/api'
import type {
  ResearchProvider,
  ResearchProviderHealth,
  ResearchSettings,
} from '@/lib/types'

const PID = 'project-1'

function resetStore() {
  useResearchSettingsStore.setState({
    settingsByProject: {},
    providersByProject: {},
    healthByProject: {},
    loadingByProject: {},
  })
}

describe('researchSettingsStore', () => {
  beforeEach(() => {
    resetStore()
    vi.restoreAllMocks()
  })

  it('fetchSettings stores the response keyed by projectId', async () => {
    const fake: ResearchSettings = {
      default_depth: 'tief',
      enabled_providers: ['kb_fts'],
      provider_settings: {},
      routing_hints: 'hint',
      updated_at: '2026-05-19T00:00:00Z',
    }
    vi.spyOn(api, 'get').mockResolvedValueOnce(fake)
    await useResearchSettingsStore.getState().fetchSettings(PID)
    expect(useResearchSettingsStore.getState().settingsByProject[PID]).toEqual(fake)
  })

  it('fetchProviders stores the provider list', async () => {
    const providers: ResearchProvider[] = [
      {
        key: 'kb_fts',
        description: 'local kb',
        typical_latency: 'fast',
        side_effect: 'read',
        default_enabled: true,
        enabled: true,
      },
      {
        key: 'confluence',
        description: 'remote',
        typical_latency: 'slow',
        side_effect: 'external',
        default_enabled: false,
        enabled: false,
      },
    ]
    vi.spyOn(api, 'get').mockResolvedValueOnce(providers)
    await useResearchSettingsStore.getState().fetchProviders(PID)
    expect(
      useResearchSettingsStore.getState().providersByProject[PID]?.length,
    ).toBe(2)
  })

  it('fetchHealth toggles loadingByProject and stores rows', async () => {
    const health: ResearchProviderHealth[] = [
      { key: 'kb_fts', ok: true, detail: 'connected', last_checked_at: 'now' },
      { key: 'confluence', ok: false, detail: 'disabled', last_checked_at: 'now' },
    ]
    let loadingDuring: boolean | undefined
    vi.spyOn(api, 'get').mockImplementationOnce(async () => {
      // Capture the loading flag mid-request.
      loadingDuring = useResearchSettingsStore.getState().loadingByProject[PID]
      return health
    })
    await useResearchSettingsStore.getState().fetchHealth(PID)
    expect(loadingDuring).toBe(true)
    expect(useResearchSettingsStore.getState().loadingByProject[PID]).toBe(false)
    expect(useResearchSettingsStore.getState().healthByProject[PID]).toEqual(health)
  })

  it('updateSettings PUTs the patch and refreshes providers', async () => {
    const updated: ResearchSettings = {
      default_depth: 'normal',
      enabled_providers: ['kb_fts', 'confluence'],
      provider_settings: {},
      routing_hints: '',
      updated_at: '2026-05-19T00:01:00Z',
    }
    const putSpy = vi.spyOn(api, 'put').mockResolvedValueOnce(updated)
    const getSpy = vi.spyOn(api, 'get').mockResolvedValueOnce([])
    await useResearchSettingsStore.getState().updateSettings(PID, {
      default_depth: 'normal',
    })
    expect(putSpy).toHaveBeenCalledWith('/research/project-1/settings', {
      default_depth: 'normal',
    })
    expect(getSpy).toHaveBeenCalled() // providers re-fetched
    expect(useResearchSettingsStore.getState().settingsByProject[PID]).toEqual(updated)
  })

  it('toggleProvider toggles the key and re-fetches providers', async () => {
    // Seed with kb_fts enabled.
    useResearchSettingsStore.setState({
      settingsByProject: {
        [PID]: {
          default_depth: 'normal',
          enabled_providers: ['kb_fts'],
          provider_settings: {},
          routing_hints: '',
          updated_at: 'now',
        },
      },
    })

    const captured: { body?: unknown } = {}
    vi.spyOn(api, 'put').mockImplementationOnce(async (_p, body) => {
      captured.body = body
      return {
        default_depth: 'normal',
        enabled_providers: ['kb_fts', 'confluence'],
        provider_settings: {},
        routing_hints: '',
        updated_at: 'now',
      } as ResearchSettings
    })
    vi.spyOn(api, 'get').mockResolvedValueOnce([])

    await useResearchSettingsStore.getState().toggleProvider(PID, 'confluence')

    expect(captured.body).toEqual({
      enabled_providers: ['kb_fts', 'confluence'],
    })
  })

  it('toggleProvider removes a key already enabled', async () => {
    useResearchSettingsStore.setState({
      settingsByProject: {
        [PID]: {
          default_depth: 'normal',
          enabled_providers: ['kb_fts', 'email'],
          provider_settings: {},
          routing_hints: '',
          updated_at: 'now',
        },
      },
    })

    const captured: { body?: unknown } = {}
    vi.spyOn(api, 'put').mockImplementationOnce(async (_p, body) => {
      captured.body = body
      return {
        default_depth: 'normal',
        enabled_providers: ['kb_fts'],
        provider_settings: {},
        routing_hints: '',
        updated_at: 'now',
      } as ResearchSettings
    })
    vi.spyOn(api, 'get').mockResolvedValueOnce([])

    await useResearchSettingsStore.getState().toggleProvider(PID, 'email')
    expect(captured.body).toEqual({ enabled_providers: ['kb_fts'] })
  })
})
