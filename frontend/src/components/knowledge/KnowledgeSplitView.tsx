import { KnowledgeGraphView } from './KnowledgeGraphView'
import { KnowledgeListView } from './KnowledgeListView'

interface KnowledgeSplitViewProps {
  projectId: string
}

export function KnowledgeSplitView({ projectId }: KnowledgeSplitViewProps) {
  return (
    <div className="flex gap-4" style={{ height: 'calc(100vh - 340px)' }}>
      {/* List (left, narrow) */}
      <div className="w-80 shrink-0 overflow-hidden">
        <KnowledgeListView projectId={projectId} compact />
      </div>

      {/* Graph (right, wide) */}
      <div className="flex-1 min-w-0">
        <KnowledgeGraphView projectId={projectId} height={Math.max(400, window.innerHeight - 340)} />
      </div>
    </div>
  )
}
