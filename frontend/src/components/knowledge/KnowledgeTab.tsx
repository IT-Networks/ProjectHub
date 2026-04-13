import { useEffect, useState } from 'react'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { useProjectStore } from '@/stores/projectStore'
import { KnowledgeToolbar } from './KnowledgeToolbar'
import { KnowledgeGraphView } from './KnowledgeGraphView'
import { KnowledgeListView } from './KnowledgeListView'
import { KnowledgeSplitView } from './KnowledgeSplitView'
import { KnowledgeItemDialog } from './KnowledgeItemDialog'
import { NodeDetailPanel } from './NodeDetailPanel'
import { DocumentScanPanel } from './DocumentScanPanel'
import { ResearchDialog } from './ResearchDialog'

interface KnowledgeTabProps {
  projectId: string
}

export function KnowledgeTab({ projectId }: KnowledgeTabProps) {
  const viewMode = useKnowledgeStore((s) => s.viewMode)
  const selectedItemId = useKnowledgeStore((s) => s.selectedItemId)
  const setSelectedItem = useKnowledgeStore((s) => s.setSelectedItem)
  const fetchStats = useKnowledgeStore((s) => s.fetchStats)
  const fetchItems = useKnowledgeStore((s) => s.fetchItems)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const currentProject = useProjectStore((s) => s.currentProject)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editItemId, setEditItemId] = useState<string | null>(null)
  const [researchOpen, setResearchOpen] = useState(false)

  useEffect(() => {
    fetchStats(projectId)
    fetchItems(projectId)
    fetchGraph(projectId)
  }, [projectId, fetchStats, fetchItems, fetchGraph])

  const handleAdd = () => {
    setEditItemId(null)
    setDialogOpen(true)
  }

  const handleEdit = (itemId: string) => {
    setEditItemId(itemId)
    setDialogOpen(true)
  }

  const handleCloseDetail = () => {
    setSelectedItem(null)
  }

  return (
    <div>
      {/* Document Scan Panel (only if docs_path set) */}
      <DocumentScanPanel
        projectId={projectId}
        docsPath={currentProject?.docs_path ?? null}
      />

      <KnowledgeToolbar projectId={projectId} onAddClick={handleAdd} onResearchClick={() => setResearchOpen(true)} />

      <div className="flex gap-4">
        <div className="min-w-0 flex-1">
          {viewMode === 'graph' && (
            <KnowledgeGraphView projectId={projectId} height={Math.max(400, window.innerHeight - 340)} />
          )}
          {viewMode === 'list' && <KnowledgeListView projectId={projectId} />}
          {viewMode === 'split' && <KnowledgeSplitView projectId={projectId} />}
        </div>

        {/* Detail panel when item selected */}
        {selectedItemId && (
          <NodeDetailPanel
            projectId={projectId}
            onEdit={handleEdit}
            onClose={handleCloseDetail}
          />
        )}
      </div>

      <KnowledgeItemDialog
        projectId={projectId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editItemId={editItemId}
      />

      <ResearchDialog
        projectId={projectId}
        open={researchOpen}
        onOpenChange={setResearchOpen}
      />
    </div>
  )
}
