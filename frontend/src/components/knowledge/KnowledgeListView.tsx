import { useEffect, useMemo } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  CATEGORY_LABELS,
  CATEGORY_COLORS,
  SOURCE_TYPE_KB_LABELS,
  CONFIDENCE_LABELS,
} from '@/lib/types'
import type { KnowledgeItem, KnowledgeCategory, KnowledgeSourceType, Confidence } from '@/lib/types'

interface KnowledgeListViewProps {
  projectId: string
  compact?: boolean
}

export function KnowledgeListView({ projectId, compact = false }: KnowledgeListViewProps) {
  const items = useKnowledgeStore((s) => s.items)
  const searchResults = useKnowledgeStore((s) => s.searchResults)
  const searchQuery = useKnowledgeStore((s) => s.searchQuery)
  const loading = useKnowledgeStore((s) => s.loading)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const selectedItemId = useKnowledgeStore((s) => s.selectedItemId)
  const setSelectedItem = useKnowledgeStore((s) => s.setSelectedItem)
  const fetchItemDetail = useKnowledgeStore((s) => s.fetchItemDetail)

  useEffect(() => {
    fetchItems(projectId)
  }, [projectId, fetchItems])

  // Show search results if searching, otherwise show all items
  const displayItems = useMemo(() => {
    if (searchQuery && searchResults.length > 0) {
      return searchResults.map((r) => r.item)
    }
    return items
  }, [items, searchResults, searchQuery])

  const handleClick = (item: KnowledgeItem) => {
    setSelectedItem(item.id)
    fetchItemDetail(projectId, item.id)
  }

  if (loading && items.length === 0) {
    return <div className="p-4 text-sm text-muted-foreground">Laden...</div>
  }

  if (displayItems.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-muted-foreground">
        <div>
          <p className="text-lg">Kein Wissen erfasst</p>
          <p className="mt-1 text-sm">Erstelle Wissenseinträge, um das Projekt-Gehirn aufzubauen.</p>
        </div>
      </div>
    )
  }

  return (
    <div className={`space-y-2 overflow-y-auto ${compact ? 'max-h-[calc(100vh-280px)]' : ''}`}>
      {displayItems.map((item) => (
        <Card
          key={item.id}
          className={`cursor-pointer p-3 transition-colors hover:bg-muted/50 ${
            selectedItemId === item.id ? 'ring-2 ring-primary' : ''
          }`}
          onClick={() => handleClick(item)}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: CATEGORY_COLORS[item.category as KnowledgeCategory] }}
                />
                <span className="truncate text-sm font-medium">{item.title}</span>
                {item.is_pinned && <span className="text-xs">📌</span>}
              </div>

              {!compact && item.content_plain && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {item.content_plain.slice(0, 150)}
                </p>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Badge
                  variant="outline"
                  className="text-[10px] px-1.5 py-0"
                  style={{ borderColor: CATEGORY_COLORS[item.category as KnowledgeCategory] }}
                >
                  {CATEGORY_LABELS[item.category as KnowledgeCategory]}
                </Badge>
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                  {SOURCE_TYPE_KB_LABELS[item.source_type as KnowledgeSourceType]}
                </Badge>
                {item.confidence !== 'medium' && (
                  <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                    {CONFIDENCE_LABELS[item.confidence as Confidence]}
                  </Badge>
                )}
                {!compact && item.tags.slice(0, 3).map((tag) => (
                  <Badge key={tag} variant="secondary" className="text-[10px] px-1.5 py-0">
                    {tag}
                  </Badge>
                ))}
                {!compact && item.tags.length > 3 && (
                  <span className="text-[10px] text-muted-foreground">+{item.tags.length - 3}</span>
                )}
              </div>
            </div>
          </div>
        </Card>
      ))}
    </div>
  )
}
