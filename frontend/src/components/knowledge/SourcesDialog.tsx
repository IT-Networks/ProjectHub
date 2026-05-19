import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SourcesPanel } from './SourcesPanel'

interface SourcesDialogProps {
  projectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * SourcesDialog — modal wrapper around SourcesPanel for the Knowledge tab.
 *
 * The settings panel is content-rich (provider list, depth toggle,
 * routing hints) — a sheet/sidebar wouldn't fit. A standard Dialog
 * with scroll-overflow gives the user enough room without forcing a
 * full-page Settings section.
 */
export function SourcesDialog({ projectId, open, onOpenChange }: SourcesDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Wissens-Quellen</DialogTitle>
        </DialogHeader>
        <SourcesPanel projectId={projectId} />
      </DialogContent>
    </Dialog>
  )
}
