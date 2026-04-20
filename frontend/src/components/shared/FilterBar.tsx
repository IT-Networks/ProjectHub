import { Search, Filter, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

export interface FilterConfig {
  searchQuery: string
  status?: string
  priority?: string
  tags?: string[]
  sortBy?: string
}

interface FilterBarProps {
  filters: FilterConfig
  onSearchChange: (query: string) => void
  onStatusChange?: (status: string) => void
  onPriorityChange?: (priority: string) => void
  onSortChange?: (sort: string) => void
  onReset: () => void
  statuses?: string[]
  priorities?: string[]
  sortOptions?: { value: string; label: string }[]
  compact?: boolean
  showAdvanced?: boolean
  onToggleAdvanced?: () => void
  className?: string
}

export function FilterBar({
  filters,
  onSearchChange,
  onStatusChange,
  onPriorityChange,
  onSortChange,
  onReset,
  statuses = [],
  priorities = [],
  sortOptions = [],
  compact = false,
  showAdvanced = false,
  onToggleAdvanced,
  className,
}: FilterBarProps) {
  const hasActiveFilters =
    filters.searchQuery ||
    filters.status ||
    filters.priority ||
    (filters.tags && filters.tags.length > 0)

  return (
    <div className={cn('space-y-2', className)}>
      {/* Main Filter Row */}
      <div className="flex gap-2 items-center">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Suchen..."
            value={filters.searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-9"
          />
        </div>

        {!compact && (
          <>
            {statuses.length > 0 && onStatusChange && (
              <Select value={filters.status || ''} onValueChange={onStatusChange}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="Status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Alle Status</SelectItem>
                  {statuses.map((status) => (
                    <SelectItem key={status} value={status}>
                      {status}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {priorities.length > 0 && onPriorityChange && (
              <Select value={filters.priority || ''} onValueChange={onPriorityChange}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="Priorität" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Alle</SelectItem>
                  {priorities.map((priority) => (
                    <SelectItem key={priority} value={priority}>
                      {priority}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {sortOptions.length > 0 && onSortChange && (
              <Select value={filters.sortBy || ''} onValueChange={onSortChange}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="Sortieren" />
                </SelectTrigger>
                <SelectContent>
                  {sortOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {onToggleAdvanced && (
              <Button
                variant={showAdvanced ? 'default' : 'secondary'}
                size="sm"
                onClick={onToggleAdvanced}
                className="gap-1.5"
              >
                <Filter className="h-4 w-4" />
                {showAdvanced ? 'Einfach' : 'Erweitert'}
              </Button>
            )}
          </>
        )}

        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onReset}
            title="Filter zurücksetzen"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>

      {/* Advanced Filters */}
      {showAdvanced && !compact && (
        <div className="rounded-lg border border-border bg-muted/30 p-3">
          <div className="text-xs font-medium text-muted-foreground mb-2">
            Erweiterte Filter
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="text-center p-2 bg-background/50 rounded">
              Unter Entwicklung
            </div>
            <div className="text-center p-2 bg-background/50 rounded">
              Mehrere Tags
            </div>
            <div className="text-center p-2 bg-background/50 rounded">
              Benutzerdefinierte Filter
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
