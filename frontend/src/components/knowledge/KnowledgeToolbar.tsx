import { useEffect, useRef, useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { CATEGORY_LABELS } from '@/lib/types'
import type { KnowledgeCategory } from '@/lib/types'

interface KnowledgeToolbarProps {
  projectId: string
  onAddClick: () => void
  onResearchClick: () => void
}

export function KnowledgeToolbar({ projectId, onAddClick, onResearchClick }: KnowledgeToolbarProps) {
  const viewMode = useKnowledgeStore((s) => s.viewMode)
  const setViewMode = useKnowledgeStore((s) => s.setViewMode)
  const filterCategory = useKnowledgeStore((s) => s.filterCategory)
  const setFilterCategory = useKnowledgeStore((s) => s.setFilterCategory)
  const searchItems = useKnowledgeStore((s) => s.searchItems)
  const searchQuery = useKnowledgeStore((s) => s.searchQuery)
  const setSearchQuery = useKnowledgeStore((s) => s.setSearchQuery)
  const clearSearch = useKnowledgeStore((s) => s.clearSearch)
  const stats = useKnowledgeStore((s) => s.stats)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)

  const [localQuery, setLocalQuery] = useState(searchQuery)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    setLocalQuery(searchQuery)
  }, [searchQuery])

  const handleSearch = (value: string) => {
    setLocalQuery(value)
    clearTimeout(debounceRef.current)
    if (!value.trim()) {
      clearSearch()
      return
    }
    debounceRef.current = setTimeout(() => {
      searchItems(projectId, value)
    }, 300)
  }

  const handleCategoryChange = (value: string) => {
    const cat = value === '__all__' ? null : value
    setFilterCategory(cat)
    // Refresh data with new filter
    setTimeout(() => {
      fetchItems(projectId)
      fetchGraph(projectId)
    }, 0)
  }

  return (
    <div className="mb-4 flex items-center gap-3">
      {/* Search */}
      <div className="relative flex-1 max-w-sm">
        <Input
          value={localQuery}
          onChange={(e) => handleSearch(e.target.value)}
          placeholder="Wissen durchsuchen..."
          className="pr-8"
        />
        {localQuery && (
          <button
            onClick={() => { setLocalQuery(''); clearSearch() }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        )}
      </div>

      {/* Category Filter */}
      <Select value={filterCategory || '__all__'} onValueChange={handleCategoryChange}>
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder="Alle Kategorien" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">Alle Kategorien</SelectItem>
          {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
            <SelectItem key={key} value={key}>{label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* View Toggle */}
      <div className="flex rounded-md border border-input">
        {([
          { mode: 'graph' as const, label: '◉' , title: 'Graph' },
          { mode: 'list' as const, label: '☰', title: 'Liste' },
          { mode: 'split' as const, label: '◫', title: 'Split' },
        ]).map(({ mode, label, title }) => (
          <button
            key={mode}
            onClick={() => setViewMode(mode)}
            title={title}
            className={`px-3 py-1.5 text-sm transition-colors ${
              viewMode === mode
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-muted'
            } ${mode === 'graph' ? 'rounded-l-md' : mode === 'split' ? 'rounded-r-md' : ''}`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Stats Badge */}
      {stats && (
        <Badge variant="secondary" className="text-xs">
          {stats.total_items} Items · {stats.total_edges} Verknüpfungen
        </Badge>
      )}

      {/* Research Button */}
      <Button variant="outline" onClick={onResearchClick} size="sm">
        Recherchieren
      </Button>

      {/* Add Button */}
      <Button onClick={onAddClick} size="sm">
        + Wissen
      </Button>
    </div>
  )
}
