import { useEffect, useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { CATEGORY_COLORS, EDGE_TYPE_LABELS } from '@/lib/types'
import type { SuggestedEdge, KnowledgeCategory, EdgeType } from '@/lib/types'

interface SuggestedLinksPanelProps {
  projectId: string
  itemId: string
}

export function SuggestedLinksPanel({ projectId, itemId }: SuggestedLinksPanelProps) {
  const suggestLinks = useKnowledgeStore((s) => s.suggestLinks)
  const createEdge = useKnowledgeStore((s) => s.createEdge)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const fetchItemDetail = useKnowledgeStore((s) => s.fetchItemDetail)

  const [suggestions, setSuggestions] = useState<SuggestedEdge[]>([])
  const [loading, setLoading] = useState(false)
  const [accepted, setAccepted] = useState<Set<string>>(new Set())

  useEffect(() => {
    setLoading(true)
    setAccepted(new Set())
    suggestLinks(projectId, itemId)
      .then(setSuggestions)
      .catch(() => setSuggestions([]))
      .finally(() => setLoading(false))
  }, [projectId, itemId, suggestLinks])

  const handleAccept = async (suggestion: SuggestedEdge) => {
    await createEdge(projectId, {
      source_item_id: itemId,
      target_item_id: suggestion.target_item_id,
      edge_type: suggestion.edge_type,
      label: suggestion.reason,
    })
    setAccepted((prev) => new Set([...prev, suggestion.target_item_id]))
    fetchGraph(projectId)
    fetchItemDetail(projectId, itemId)
  }

  if (loading) {
    return <p className="text-xs text-muted-foreground">Suche Verknüpfungen...</p>
  }

  if (suggestions.length === 0) {
    return <p className="text-xs text-muted-foreground">Keine Vorschläge gefunden</p>
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-medium text-muted-foreground uppercase">
        Vorgeschlagene Verknüpfungen ({suggestions.length})
      </h4>
      {suggestions.map((s) => (
        <div
          key={s.target_item_id}
          className="flex items-center gap-2 rounded border border-border p-2 text-xs"
        >
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: CATEGORY_COLORS[s.target_category as KnowledgeCategory] }}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{s.target_title}</p>
            <p className="text-muted-foreground">{s.reason}</p>
          </div>
          <Badge variant="outline" className="text-[10px] shrink-0">
            {EDGE_TYPE_LABELS[s.edge_type as EdgeType]}
          </Badge>
          {accepted.has(s.target_item_id) ? (
            <span className="text-green-500 text-xs">✓</span>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-2 text-[10px]"
              onClick={() => handleAccept(s)}
            >
              Verknüpfen
            </Button>
          )}
        </div>
      ))}
    </div>
  )
}
