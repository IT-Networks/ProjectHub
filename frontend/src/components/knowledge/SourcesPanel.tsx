import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
import { useResearchSettingsStore } from '@/stores/researchSettingsStore'
import {
  RESEARCH_DEPTH_LABELS,
  type ResearchDepth,
  type ResearchProvider,
  type ResearchProviderHealth,
} from '@/lib/types'

/**
 * SourcesPanel — Per-Projekt "Wissens-Quellen"-Tab (P11).
 *
 * Liste der registrierten Provider mit Toggle-Buttons, Default-Tiefe-
 * Auswahl, freier Routing-Hint-Textarea und Health-Refresh-Button.
 * Keine Per-Provider-Settings-Formulare in v1 — die Default-Settings
 * werden im Backend pro Provider sinnvoll gefüllt. Komplexere Settings
 * (z.B. Confluence-Spaces) kommen in P11.1 wenn der Use-Case real ist.
 */
interface SourcesPanelProps {
  projectId: string
}

type GroupKey = 'local' | 'internal' | 'external'

const LOCAL_KEYS = new Set([
  'kb_fts',
  'project_documents',
  'project_notes',
  'chat_history',
])

const INTERNAL_KEYS = new Set([
  'confluence',
  'confluence_search',
  'email',
  'webex',
  'jira',
  'handbook',
  'log_servers',
  'code_graph',
  'github',
  'jenkins',
  'iq',
  'mq',
])

const GROUP_LABELS: Record<GroupKey, string> = {
  local: 'Lokale Quellen (ProjectHub-DB)',
  internal: 'Interne Systeme (via AI-Assist)',
  external: 'Externe Quellen',
}

function groupOf(key: string): GroupKey {
  if (LOCAL_KEYS.has(key)) return 'local'
  if (INTERNAL_KEYS.has(key)) return 'internal'
  return 'external'
}

function healthBadge(h: ResearchProviderHealth | undefined): string {
  if (!h) return '⚪'
  if (h.ok) return '🟢'
  if (h.detail === 'disabled') return '⚪'
  return '🔴'
}

export function SourcesPanel({ projectId }: SourcesPanelProps) {
  const settings = useResearchSettingsStore((s) => s.settingsByProject[projectId])
  const providers = useResearchSettingsStore((s) => s.providersByProject[projectId])
  const health = useResearchSettingsStore((s) => s.healthByProject[projectId])
  const loading = useResearchSettingsStore((s) => s.loadingByProject[projectId])
  const fetchSettings = useResearchSettingsStore((s) => s.fetchSettings)
  const fetchProviders = useResearchSettingsStore((s) => s.fetchProviders)
  const fetchHealth = useResearchSettingsStore((s) => s.fetchHealth)
  const updateSettings = useResearchSettingsStore((s) => s.updateSettings)
  const toggleProvider = useResearchSettingsStore((s) => s.toggleProvider)

  const [routingHintsLocal, setRoutingHintsLocal] = useState<string>('')
  const [routingHintsDirty, setRoutingHintsDirty] = useState(false)

  useEffect(() => {
    void fetchSettings(projectId)
    void fetchProviders(projectId)
  }, [projectId, fetchSettings, fetchProviders])

  useEffect(() => {
    if (!routingHintsDirty && settings) {
      setRoutingHintsLocal(settings.routing_hints || '')
    }
  }, [settings, routingHintsDirty])

  const healthByKey = useMemo(() => {
    const m = new Map<string, ResearchProviderHealth>()
    for (const h of health ?? []) m.set(h.key, h)
    return m
  }, [health])

  const grouped = useMemo(() => {
    const buckets: Record<GroupKey, ResearchProvider[]> = {
      local: [],
      internal: [],
      external: [],
    }
    for (const p of providers ?? []) {
      buckets[groupOf(p.key)].push(p)
    }
    return buckets
  }, [providers])

  const handleDepthChange = async (depth: ResearchDepth) => {
    await updateSettings(projectId, { default_depth: depth })
  }

  const handleSaveRoutingHints = async () => {
    await updateSettings(projectId, { routing_hints: routingHintsLocal })
    setRoutingHintsDirty(false)
  }

  const handleHealthRefresh = async () => {
    await fetchHealth(projectId)
  }

  const currentDepth: ResearchDepth = settings?.default_depth ?? 'normal'

  return (
    <div className="space-y-6">
      {/* Default-Tiefe */}
      <section>
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Default-Tiefe für neue Recherchen
        </h3>
        <div className="flex gap-2">
          {(['normal', 'tief'] as ResearchDepth[]).map((d) => (
            <Button
              key={d}
              variant={currentDepth === d ? 'default' : 'outline'}
              onClick={() => void handleDepthChange(d)}
              data-testid={`depth-${d}`}
            >
              {RESEARCH_DEPTH_LABELS[d]}
            </Button>
          ))}
        </div>
      </section>

      {/* Provider-Listen */}
      {(['local', 'internal', 'external'] as GroupKey[]).map((group) => {
        const items = grouped[group]
        if (!items || items.length === 0) return null
        return (
          <section key={group}>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                {GROUP_LABELS[group]}
              </h3>
              {group !== 'local' && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleHealthRefresh()}
                  disabled={loading}
                  data-testid={`health-refresh-${group}`}
                >
                  {loading ? 'Prüfe…' : 'Health-Check'}
                </Button>
              )}
            </div>
            <ul className="space-y-1">
              {items.map((p) => (
                <li
                  key={p.key}
                  className="flex items-start justify-between gap-3 rounded border bg-card p-3"
                  data-testid={`provider-row-${p.key}`}
                >
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm">{p.key}</span>
                      <span aria-label="health" data-testid={`health-${p.key}`}>
                        {healthBadge(healthByKey.get(p.key))}
                      </span>
                      <Badge variant="outline" className="text-xs">
                        {p.typical_latency}
                      </Badge>
                      <Badge
                        variant={p.side_effect === 'external' ? 'destructive' : 'secondary'}
                        className="text-xs"
                      >
                        {p.side_effect}
                      </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground mt-1">
                      {p.description}
                    </p>
                    {healthByKey.get(p.key)?.detail &&
                      healthByKey.get(p.key)?.detail !== 'connected' && (
                        <p className="text-xs text-muted-foreground mt-1 font-mono">
                          {healthByKey.get(p.key)?.detail}
                        </p>
                      )}
                  </div>
                  <Button
                    variant={p.enabled ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => void toggleProvider(projectId, p.key)}
                    data-testid={`toggle-${p.key}`}
                  >
                    {p.enabled ? 'An' : 'Aus'}
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )
      })}

      {/* Routing-Hints */}
      <section>
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Routing-Hinweise für den Planner
        </h3>
        <Textarea
          value={routingHintsLocal}
          onChange={(e) => {
            setRoutingHintsLocal(e.target.value)
            setRoutingHintsDirty(true)
          }}
          placeholder="z.B. „Bei Auth-Themen immer Confluence vor Code-Graph"."
          className="min-h-[80px]"
          data-testid="routing-hints"
        />
        {routingHintsDirty && (
          <div className="flex justify-end mt-2">
            <Button
              size="sm"
              onClick={() => void handleSaveRoutingHints()}
              data-testid="save-routing-hints"
            >
              Speichern
            </Button>
          </div>
        )}
      </section>
    </div>
  )
}
