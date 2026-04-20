import { NodeViewWrapper, NodeViewContent } from '@tiptap/react'

export function CustomTaskItemView(props: any) {
  const { node, updateAttributes } = props
  const isChecked = node.attrs.checked

  return (
    <NodeViewWrapper as="li" className="flex items-center gap-2 list-none">
      <input
        type="checkbox"
        checked={isChecked}
        onChange={(e) => updateAttributes({ checked: e.target.checked })}
        className="w-4 h-4 shrink-0 cursor-pointer"
      />
      <NodeViewContent className="flex-1" />
    </NodeViewWrapper>
  )
}
