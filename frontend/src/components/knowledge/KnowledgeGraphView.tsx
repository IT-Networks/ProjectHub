import { useCallback, useEffect, useRef, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { CATEGORY_COLORS } from '@/lib/types'
import type { KnowledgeCategory } from '@/lib/types'

const EDGE_COLORS: Record<string, string> = {
  related: '#6b7280',
  references: '#3b82f6',
  based_on: '#10b981',
  extends: '#f59e0b',
}

interface KnowledgeGraphViewProps {
  projectId: string
  height?: number
}

export function KnowledgeGraphView({ projectId, height = 500 }: KnowledgeGraphViewProps) {
  const graphData = useKnowledgeStore((s) => s.graphData)
  const fetchGraph = useKnowledgeStore((s) => s.fetchGraph)
  const selectedItemId = useKnowledgeStore((s) => s.selectedItemId)
  const setSelectedItem = useKnowledgeStore((s) => s.setSelectedItem)
  const fetchItemDetail = useKnowledgeStore((s) => s.fetchItemDetail)
  const graphRef = useRef<any>(null)

  useEffect(() => {
    fetchGraph(projectId)
  }, [projectId, fetchGraph])

  // Transform data for react-force-graph-2d
  const forceData = useMemo(() => {
    if (!graphData) return { nodes: [], links: [] }
    return {
      nodes: graphData.nodes.map((n) => ({
        id: n.id,
        title: n.title,
        category: n.category,
        is_pinned: n.is_pinned,
        source_type: n.source_type,
        edge_count: n.edge_count,
        val: Math.max(2, 1 + Math.log2(1 + n.edge_count)) * 3,
      })),
      links: graphData.edges.map((e) => ({
        source: e.source,
        target: e.target,
        type: e.type,
        label: e.label,
        id: e.id,
      })),
    }
  }, [graphData])

  const handleNodeClick = useCallback((node: any) => {
    setSelectedItem(node.id)
    fetchItemDetail(projectId, node.id)
  }, [projectId, setSelectedItem, fetchItemDetail])

  const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = node.title?.length > 20 ? node.title.slice(0, 20) + '...' : node.title
    const fontSize = 11 / globalScale
    const nodeSize = node.val || 4
    const color = CATEGORY_COLORS[node.category as KnowledgeCategory] || '#6b7280'
    const isSelected = node.id === selectedItemId

    // Node circle
    ctx.beginPath()
    ctx.arc(node.x, node.y, nodeSize, 0, 2 * Math.PI)
    ctx.fillStyle = color
    ctx.globalAlpha = 0.85
    ctx.fill()
    ctx.globalAlpha = 1

    // Selected ring
    if (isSelected) {
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 2 / globalScale
      ctx.stroke()
    }

    // Pinned indicator
    if (node.is_pinned) {
      ctx.strokeStyle = '#eab308'
      ctx.lineWidth = 1.5 / globalScale
      ctx.stroke()
    }

    // Document icon
    if (node.source_type === 'document') {
      ctx.font = `${fontSize * 0.8}px sans-serif`
      ctx.textAlign = 'center'
      ctx.fillStyle = '#ffffff'
      ctx.fillText('📄', node.x, node.y + fontSize * 0.3)
    }

    // Label
    if (globalScale > 0.6) {
      ctx.font = `${fontSize}px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = 'hsl(0, 0%, 70%)'
      ctx.fillText(label || '', node.x, node.y + nodeSize + 2)
    }
  }, [selectedItemId])

  const linkCanvasObject = useCallback((link: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const start = link.source
    const end = link.target
    if (!start?.x || !end?.x) return

    const color = EDGE_COLORS[link.type] || '#6b7280'

    ctx.beginPath()
    ctx.moveTo(start.x, start.y)
    ctx.lineTo(end.x, end.y)
    ctx.strokeStyle = color
    ctx.globalAlpha = 0.4
    ctx.lineWidth = 1 / globalScale

    if (link.type === 'references' || link.type === 'based_on') {
      ctx.setLineDash([5 / globalScale, 3 / globalScale])
    } else {
      ctx.setLineDash([])
    }

    ctx.stroke()
    ctx.globalAlpha = 1
    ctx.setLineDash([])
  }, [])

  // Fit to view on data change
  useEffect(() => {
    if (graphRef.current && forceData.nodes.length > 0) {
      setTimeout(() => {
        graphRef.current?.zoomToFit(400, 40)
      }, 500)
    }
  }, [forceData])

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <p className="text-sm">Keine Wissenseinträge vorhanden</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-background" style={{ height }}>
      <ForceGraph2D
        ref={graphRef}
        graphData={forceData}
        width={undefined}
        height={height}
        nodeCanvasObject={nodeCanvasObject}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        nodeLabel={(node: any) => `${node.title} [${node.category}]`}
        cooldownTicks={100}
        d3AlphaDecay={0.05}
        d3VelocityDecay={0.3}
        backgroundColor="transparent"
      />
    </div>
  )
}
