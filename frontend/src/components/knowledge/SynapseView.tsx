import { useEffect } from 'react'
import { useSynapseStore } from '@/stores/synapseStore'
import { SynapseGenerateBar } from './SynapseGenerateBar'
import { ReviewQueuePanel } from './ReviewQueuePanel'
import { GlobalAskBox } from './GlobalAskBox'
import { SynapseCard } from './SynapseCard'

interface SynapseViewProps {
  projectId: string
}

/**
 * Synapsen-Ansicht des Wissen-Tabs — die Synthese-Schicht über den
 * flachen Wissenseinträgen: globale Frage, Generierungs-Steuerung,
 * Review-Queue und die Liste der validierten Synapsen.
 */
export function SynapseView({ projectId }: SynapseViewProps) {
  const synapses = useSynapseStore((s) => s.synapsesByProject[projectId])
  const loading = useSynapseStore((s) => s.loadingByProject[projectId])
  const fetchSynapses = useSynapseStore((s) => s.fetchSynapses)

  useEffect(() => {
    void fetchSynapses(projectId)
  }, [projectId, fetchSynapses])

  return (
    <div>
      <GlobalAskBox projectId={projectId} />
      <SynapseGenerateBar projectId={projectId} />
      <ReviewQueuePanel projectId={projectId} />

      {loading && !synapses && (
        <p className="py-8 text-center text-sm text-muted-foreground">Lädt…</p>
      )}

      {synapses && synapses.length === 0 && (
        <div className="rounded-md border border-dashed border-border py-10 text-center">
          <p className="text-sm text-muted-foreground">
            Noch keine Synapsen. Starte eine Wissens-Synthese, um aus den
            Einträgen verdichtete, validierte Erkenntnisse zu erzeugen.
          </p>
        </div>
      )}

      {synapses && synapses.length > 0 && (
        <div className="space-y-2">
          {synapses.map((synapse) => (
            <SynapseCard
              key={synapse.id}
              projectId={projectId}
              synapse={synapse}
            />
          ))}
        </div>
      )}
    </div>
  )
}
