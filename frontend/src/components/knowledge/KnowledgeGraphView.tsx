import { useCallback, useEffect, useLayoutEffect, useRef, useMemo, useState } from 'react'
import ForceGraph2D, { type NodeObject, type LinkObject, type ForceGraphMethods } from 'react-force-graph-2d'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { CATEGORY_COLORS } from '@/lib/types'
import type { KnowledgeCategory } from '@/lib/types'

const EDGE_COLORS: Record<string, string> = {
  related: '#6b7280',
  references: '#3b82f6',
  based_on: '#10b981',
  extends: '#f59e0b',
}

// Plain data shapes we build for react-force-graph-2d. The library wraps
// these in NodeObject/LinkObject, which add the layout-managed x/y/vx/vy
// fields and resolve link source/target from id strings to node objects.
interface GraphNodeData {
  id: string
  title: string
  category: string
  is_pinned: boolean
  source_type: string
  edge_count: number
  val: number
}

interface GraphLinkData {
  source: string
  target: string
  type: string
  label: string
  id: string
}

type GraphNode = NodeObject<GraphNodeData>
type GraphLink = LinkObject<GraphNodeData, GraphLinkData>

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
  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    fetchGraph(projectId)
  }, [projectId, fetchGraph])

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Transform data for react-force-graph-2d
  const forceData = useMemo<{ nodes: GraphNodeData[]; links: GraphLinkData[] }>(() => {
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

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelectedItem(node.id)
    fetchItemDetail(projectId, node.id)
  }, [projectId, setSelectedItem, fetchItemDetail])

  const nodeCanvasObject = useCallback((node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = node.title?.length > 20 ? node.title.slice(0, 20) + '...' : node.title
    const fontSize = 11 / globalScale
    const nodeSize = node.val || 4
    const color = CATEGORY_COLORS[node.category as KnowledgeCategory] || '#6b7280'
    const isSelected = node.id === selectedItemId
    const x = node.x ?? 0
    const y = node.y ?? 0

    // Node circle
    ctx.beginPath()
    ctx.arc(x, y, nodeSize, 0, 2 * Math.PI)
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
      ctx.fillText('📄', x, y + fontSize * 0.3)
    }

    // Label
    if (globalScale > 0.6) {
      ctx.font = `${fontSize}px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = 'hsl(0, 0%, 70%)'
      ctx.fillText(label || '', x, y + nodeSize + 2)
    }
  }, [selectedItemId])

  const linkCanvasObject = useCallback((link: GraphLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
    // Post-layout the library replaces the id strings with node objects;
    // the loose lib types (string index signatures) need a narrow cast.
    const start = link.source as NodeObject<GraphNodeData> | string | undefined
    const end = link.target as NodeObject<GraphNodeData> | string | undefined
    if (typeof start !== 'object' || typeof end !== 'object') return
    if (start.x == null || start.y == null || end.x == null || end.y == null) return

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
    <div
      ref={containerRef}
      className="relative overflow-hidden rounded-lg border border-border bg-background"
      style={{ height }}
    >
      {width > 0 && (
        <ForceGraph2D
          ref={graphRef}
          graphData={forceData}
          width={width}
          height={height}
          nodeCanvasObject={nodeCanvasObject}
          linkCanvasObject={linkCanvasObject}
          onNodeClick={handleNodeClick}
          nodeLabel={(node: GraphNode) => `${node.title} [${node.category}]`}
          cooldownTicks={100}
          d3AlphaDecay={0.05}
          d3VelocityDecay={0.3}
          backgroundColor="transparent"
        />
      )}
    </div>
  )
}
