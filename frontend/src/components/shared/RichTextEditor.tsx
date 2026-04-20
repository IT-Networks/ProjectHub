import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import TaskList from '@tiptap/extension-task-list'
import TaskItem from '@tiptap/extension-task-item'
import Link from '@tiptap/extension-link'
import Highlight from '@tiptap/extension-highlight'
import { convertTaskListHTML } from '@/lib/taskUtils'
import { cn } from '@/lib/utils'

interface Props {
  content: string
  onChange: (content: string) => void
  placeholder?: string
  className?: string
}

function ToolbarButton({ active, onClick, children }: { active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onMouseDown={(e) => { e.preventDefault(); onClick() }}
      className={cn(
        'rounded px-2 py-1 text-xs transition-colors',
        active ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-accent/50'
      )}
    >
      {children}
    </button>
  )
}

export function RichTextEditor({ content, onChange, placeholder = 'Schreiben...', className }: Props) {
  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Link.configure({ openOnClick: false }),
      Highlight,
    ],
    content,
    onUpdate: ({ editor }) => {
      const html = editor.getHTML()
      const convertedHtml = convertTaskListHTML(html)
      onChange(convertedHtml)
    },
  })

  if (!editor) return null

  return (
    <>
      <style>{`
        .rich-task-editor li[data-type="taskItem"] {
          display: flex !important;
          align-items: flex-start !important;
          gap: 0.5rem !important;
          list-style: none !important;
        }
        .rich-task-editor li[data-type="taskItem"] > label {
          flex-shrink: 0 !important;
        }
        .rich-task-editor li[data-type="taskItem"] > div {
          flex: 1 !important;
        }
        .rich-task-editor li[data-type="taskItem"] > div > p {
          display: inline !important;
          margin: 0 !important;
        }
      `}</style>
      <div className={cn('rounded-lg border border-input rich-task-editor', className)}>
        {/* Toolbar */}
        <div className="flex flex-wrap gap-0.5 border-b border-border px-2 py-1">
        <ToolbarButton active={editor.isActive('bold')} onClick={() => editor.chain().focus().toggleBold().run()}>
          <strong>B</strong>
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('italic')} onClick={() => editor.chain().focus().toggleItalic().run()}>
          <em>I</em>
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('strike')} onClick={() => editor.chain().focus().toggleStrike().run()}>
          <s>S</s>
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('highlight')} onClick={() => editor.chain().focus().toggleHighlight().run()}>
          H
        </ToolbarButton>
        <span className="mx-1 w-px bg-border" />
        <ToolbarButton active={editor.isActive('bulletList')} onClick={() => editor.chain().focus().toggleBulletList().run()}>
          Liste
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('orderedList')} onClick={() => editor.chain().focus().toggleOrderedList().run()}>
          1. Liste
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('taskList')} onClick={() => editor.chain().focus().toggleTaskList().run()}>
          Aufgaben
        </ToolbarButton>
        <span className="mx-1 w-px bg-border" />
        <ToolbarButton active={editor.isActive('heading', { level: 2 })} onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
          H2
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('heading', { level: 3 })} onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
          H3
        </ToolbarButton>
        <ToolbarButton active={editor.isActive('codeBlock')} onClick={() => editor.chain().focus().toggleCodeBlock().run()}>
          Code
        </ToolbarButton>
      </div>

      {/* Editor */}
      <EditorContent
        editor={editor}
        className="prose prose-sm prose-invert max-w-none px-4 py-3 focus:outline-none [&_.tiptap]:min-h-[120px] [&_.tiptap]:outline-none [&_.tiptap_h2]:text-lg [&_.tiptap_h2]:font-bold [&_.tiptap_h2]:my-3 [&_.tiptap_h3]:text-base [&_.tiptap_h3]:font-bold [&_.tiptap_h3]:my-2 [&_.tiptap_ul:not([class*='task'])]:list-disc [&_.tiptap_ul:not([class*='task'])]:pl-4 [&_.tiptap_ol]:list-decimal [&_.tiptap_ol]:pl-4 [&_.tiptap_li]:my-1 [&_.tiptap_p.is-editor-empty:first-child::before]:text-muted-foreground [&_.tiptap_p.is-editor-empty:first-child::before]:content-[attr(data-placeholder)] [&_.tiptap_p.is-editor-empty:first-child::before]:float-left [&_.tiptap_p.is-editor-empty:first-child::before]:pointer-events-none [&_.tiptap_p.is-editor-empty:first-child::before]:h-0"
      />
    </div>
    </>
  )
}
