import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useResearchStore } from './researchStore'
import { api } from '@/lib/api'
import type {
  ResearchFinding,
  ResearchRunSummary,
  ResearchStartRunResponse,
} from '@/lib/types'

const PID = 'project-1'
const RID = 'run-abc'

function resetStore() {
  useResearchStore.setState({
    runsByProject: {},
    activeRunByProject: {},
    detailByRun: {},
    liveFindings: {},
    livePhase: {},
    livePressure: {},
    liveCounts: {},
  })
}

function makeRun(overrides: Partial<ResearchRunSummary> = {}): ResearchRunSummary {
  return {
    id: RID,
    project_id: PID,
    topic: 't',
    depth: 'normal',
    mode: 'auto',
    status: 'running',
    phase: 'planning',
    current_hop: 0,
    sub_query_count: 0,
    finding_count: 0,
    validated_count: 0,
    persisted_count: 0,
    flagged_count: 0,
    rejected_count: 0,
    synapse_run_id: null,
    started_at: '2026-05-19T00:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

function makeFinding(
  overrides: Partial<ResearchFinding> = {},
): ResearchFinding {
  return {
    id: `f-${Math.random()}`,
    sub_query_id: 'sq1',
    provider_key: 'kb_fts',
    source_ref: 'kb:1',
    title: 't',
    snippet: 's',
    url: null,
    timestamp: null,
    author: null,
    status: 'candidate',
    confidence: 0.8,
    knowledge_item_id: null,
    created_at: 'now',
    updated_at: 'now',
    ...overrides,
  }
}

describe('researchStore', () => {
  beforeEach(() => {
    resetStore()
    vi.restoreAllMocks()
  })

  it('startRun POSTs the request + resets live state for the new run', async () => {
    const fakeResp: ResearchStartRunResponse = {
      run_id: RID,
      started: true,
      depth: 'normal',
    }
    vi.spyOn(api, 'post').mockResolvedValueOnce(fakeResp)
    vi.spyOn(api, 'get').mockResolvedValueOnce([])  // fetchActive
    const res = await useResearchStore.getState().startRun(PID, { topic: 'x' })
    expect(res?.run_id).toBe(RID)
    // resetLive primed the run-keyed scratch records.
    expect(useResearchStore.getState().liveFindings[RID]).toEqual([])
    expect(useResearchStore.getState().livePhase[RID]).toBe('planning')
    expect(useResearchStore.getState().liveCounts[RID]).toEqual({
      findings: 0, persisted: 0, flagged: 0, rejected: 0,
    })
  })

  it('fetchRuns populates runsByProject', async () => {
    const runs = [makeRun({ status: 'ok' }), makeRun({ id: 'r2', status: 'error' })]
    vi.spyOn(api, 'get').mockResolvedValueOnce(runs)
    await useResearchStore.getState().fetchRuns(PID)
    expect(useResearchStore.getState().runsByProject[PID]?.length).toBe(2)
  })

  it('fetchActive returns the first running run or null', async () => {
    vi.spyOn(api, 'get')
      .mockResolvedValueOnce([makeRun()])  // running
      .mockResolvedValueOnce([])             // none
    const first = await useResearchStore.getState().fetchActive(PID)
    expect(first?.id).toBe(RID)
    const second = await useResearchStore.getState().fetchActive(PID)
    expect(second).toBeNull()
  })

  it('pushFinding appends + bumps counters by status', () => {
    const store = useResearchStore.getState()
    store.resetLive(RID)
    store.pushFinding(RID, makeFinding({ status: 'candidate' }))
    store.pushFinding(RID, makeFinding({ status: 'persisted' }))
    store.pushFinding(RID, makeFinding({ status: 'flagged' }))
    const c = useResearchStore.getState().liveCounts[RID]
    expect(c).toEqual({ findings: 3, persisted: 1, flagged: 1, rejected: 0 })
    expect(useResearchStore.getState().liveFindings[RID]?.length).toBe(3)
  })

  it('cancelRun POSTs the cancel endpoint', async () => {
    const spy = vi.spyOn(api, 'post').mockResolvedValueOnce({ cancelled: true })
    await useResearchStore.getState().cancelRun(RID)
    expect(spy).toHaveBeenCalledWith('/research/runs/run-abc/cancel')
  })

  it('acceptFinding POSTs accept + refetches detail', async () => {
    const postSpy = vi.spyOn(api, 'post').mockResolvedValueOnce({ ok: true })
    const getSpy = vi.spyOn(api, 'get').mockResolvedValueOnce({
      run: makeRun(), sub_queries: [], findings: [], token_usage: {},
    })
    await useResearchStore.getState().acceptFinding(RID, 'fid-1', 'looks ok')
    expect(postSpy).toHaveBeenCalledWith(
      '/research/runs/run-abc/findings/fid-1/accept',
      { note: 'looks ok' },
    )
    expect(getSpy).toHaveBeenCalled() // detail re-fetched
  })

  it('rejectFinding POSTs reject + refetches detail', async () => {
    const postSpy = vi.spyOn(api, 'post').mockResolvedValueOnce({ ok: true })
    vi.spyOn(api, 'get').mockResolvedValueOnce({
      run: makeRun(), sub_queries: [], findings: [], token_usage: {},
    })
    await useResearchStore.getState().rejectFinding(RID, 'fid-2')
    expect(postSpy).toHaveBeenCalledWith(
      '/research/runs/run-abc/findings/fid-2/reject',
      { note: undefined },
    )
  })

  it('setLivePhase + setLivePressure update only their keys', () => {
    useResearchStore.getState().setLivePhase(RID, 'lateral')
    useResearchStore.getState().setLivePressure(RID, 'tight')
    expect(useResearchStore.getState().livePhase[RID]).toBe('lateral')
    expect(useResearchStore.getState().livePressure[RID]).toBe('tight')
  })
})
