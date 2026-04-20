import { useState, useCallback } from 'react'

interface DragItem {
  id: string
  index: number
}

interface UseDragReorderReturn {
  draggedId: string | null
  dragOverId: string | null
  handleDragStart: (id: string, index: number) => void
  handleDragOver: (id: string, event: React.DragEvent) => void
  handleDragLeave: () => void
  handleDrop: (id: string, onReorder: (from: number, to: number) => void) => void
  handleDragEnd: () => void
}

export function useDragReorder(): UseDragReorderReturn {
  const [draggedId, setDraggedId] = useState<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null)

  const handleDragStart = useCallback(
    (id: string, index: number) => {
      setDraggedId(id)
      setDraggedIndex(index)
    },
    []
  )

  const handleDragOver = useCallback((id: string, event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDragOverId(id)
  }, [])

  const handleDragLeave = useCallback(() => {
    setDragOverId(null)
  }, [])

  const handleDrop = useCallback(
    (id: string, onReorder: (from: number, to: number) => void) => {
      if (draggedIndex !== null) {
        const toIndex = parseInt(id.split('-')[1] || '0')
        if (draggedIndex !== toIndex) {
          onReorder(draggedIndex, toIndex)
        }
      }
      setDraggedId(null)
      setDragOverId(null)
      setDraggedIndex(null)
    },
    [draggedIndex]
  )

  const handleDragEnd = useCallback(() => {
    setDraggedId(null)
    setDragOverId(null)
    setDraggedIndex(null)
  }, [])

  return {
    draggedId,
    dragOverId,
    handleDragStart,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handleDragEnd,
  }
}
