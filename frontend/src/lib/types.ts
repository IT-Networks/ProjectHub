// === Projekte ===
export interface Project {
  id: string
  name: string
  description: string
  status: 'aktiv' | 'pausiert' | 'archiviert'
  color: string
  tags: string[]
  sort_order: number
  docs_path: string | null
  sources: DataSourceLink[]
  counts: ProjectCounts
  created_at: string
  updated_at: string
}

export interface ProjectListItem {
  id: string
  name: string
  description: string
  status: 'aktiv' | 'pausiert' | 'archiviert'
  color: string
  tags: string[]
  sort_order: number
  docs_path: string | null
  source_count: number
  todo_open: number
  created_at: string
  updated_at: string
}

export interface ProjectCounts {
  todos_open: number
  todos_done: number
  notes: number
  research: number
}

export interface ProjectCreate {
  name: string
  description?: string
  status?: string
  color?: string
  tags?: string[]
  docs_path?: string | null
}

export interface ProjectUpdate {
  name?: string
  description?: string
  status?: string
  color?: string
  tags?: string[]
  sort_order?: number
  docs_path?: string | null
}

// === Datenquellen ===
export type SourceType =
  | 'jenkins_job'
  | 'github_repo'
  | 'git_repo'
  | 'jira_project'
  | 'confluence_space'
  | 'email_folder'
  | 'webex_room'

export interface DataSourceLink {
  id: string
  source_type: SourceType
  source_config: Record<string, string>
  display_name: string
  created_at: string
}

export interface DataSourceLinkCreate {
  source_type: SourceType
  source_config: Record<string, string>
  display_name?: string
}

// === Todos ===
export type TodoStatus = 'backlog' | 'in_progress' | 'review' | 'done'
export type Priority = 'high' | 'medium' | 'low'

export interface Todo {
  id: string
  project_id: string | null
  title: string
  description: string
  status: TodoStatus
  priority: Priority
  deadline: string | null
  kanban_order: number
  tags: string[]
  source: 'manual' | 'email' | 'webex'
  source_ref: string | null
  ai_analysis: string | null
  created_at: string
  updated_at: string
}

// === Notizen ===
export interface Note {
  id: string
  project_id: string
  title: string
  content: string
  content_format: 'tiptap' | 'html' | 'markdown'
  deadline: string | null
  is_pinned: boolean
  tags: string[]
  sort_order: number
  created_at: string
  updated_at: string
}

// === Widget ===
export type WidgetType =
  | 'build_status'
  | 'pr_list'
  | 'todo_count'
  | 'note'
  | 'project_status'
  | 'jira_issues'
  | 'activity'
  | 'kanban_mini'
  | 'inbox_preview'
  | 'research_history'
  | 'deadline_calendar'

export interface WidgetConfig {
  id: string
  widget_type: WidgetType
  grid_col: number
  grid_row: number
  grid_width: number
  grid_height: number
  config: Record<string, unknown>
  is_visible: boolean
}

// === Nachrichten ===
export interface LinkedMessage {
  id: string
  link_target: 'project' | 'todo' | 'note'
  target_id: string
  source: 'email' | 'webex'
  source_ref: string
  subject: string
  sender: string
  date: string
  snippet: string
  created_at: string
}

// === Todo-Queue ===
export interface QueueItem {
  id: string
  suggested_title: string
  suggested_description: string
  suggested_priority: Priority
  suggested_deadline: string | null
  suggested_project_id: string | null
  source: 'email' | 'webex'
  source_subject: string
  source_sender: string
  source_date: string
  ai_analysis: string
  ai_confidence: number
  queue_status: 'pending' | 'accepted' | 'rejected'
  created_at: string
}

// === Navigation ===
export interface NavItem {
  label: string
  path: string
  icon: string
  badge?: number
}

// === Labels (Deutsch) ===
export const STATUS_LABELS: Record<string, string> = {
  aktiv: 'Aktiv',
  pausiert: 'Pausiert',
  archiviert: 'Archiviert',
  backlog: 'Backlog',
  in_progress: 'In Arbeit',
  review: 'Review',
  done: 'Erledigt',
}

export const PRIORITY_LABELS: Record<string, string> = {
  high: 'Hoch',
  medium: 'Mittel',
  low: 'Niedrig',
}

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  jenkins_job: 'Jenkins Job',
  github_repo: 'GitHub Repo',
  git_repo: 'Git Repository',
  jira_project: 'Jira Projekt',
  confluence_space: 'Confluence Space',
  email_folder: 'Email-Ordner',
  webex_room: 'Webex Raum',
}

// === Knowledge Base ===
export type KnowledgeCategory =
  | 'architecture'
  | 'business_logic'
  | 'infrastructure'
  | 'process'
  | 'decision'
  | 'reference'
  | 'custom'

export type KnowledgeSourceType =
  | 'manual'
  | 'research'
  | 'note_import'
  | 'email_extract'
  | 'chat_extract'
  | 'confluence'
  | 'document'

export type EdgeType = 'related' | 'references' | 'based_on' | 'extends'
export type Confidence = 'high' | 'medium' | 'low'

export interface KnowledgeItem {
  id: string
  project_id: string
  title: string
  content: string
  content_plain: string
  category: KnowledgeCategory
  source_type: KnowledgeSourceType
  source_ref: string | null
  tags: string[]
  confidence: Confidence
  metadata: Record<string, unknown>
  is_pinned: boolean
  created_at: string
  updated_at: string
}

export interface KnowledgeItemCreate {
  title: string
  content?: string
  category?: KnowledgeCategory
  tags?: string[]
  source_type?: KnowledgeSourceType
  source_ref?: string | null
  confidence?: Confidence
  metadata?: Record<string, unknown>
}

export interface KnowledgeItemUpdate {
  title?: string
  content?: string
  category?: KnowledgeCategory
  tags?: string[]
  confidence?: Confidence
  metadata?: Record<string, unknown>
  is_pinned?: boolean
}

export interface KnowledgeEdge {
  id: string
  source_item_id: string
  target_item_id: string
  edge_type: EdgeType
  label: string | null
  created_at: string
}

export interface KnowledgeItemDetail extends KnowledgeItem {
  edges: KnowledgeEdge[]
  neighbors: { id: string; title: string; category: string }[]
}

export interface GraphNode {
  id: string
  title: string
  category: KnowledgeCategory
  tags: string[]
  is_pinned: boolean
  source_type: KnowledgeSourceType
  edge_count: number
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: EdgeType
  label: string | null
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface KnowledgeStats {
  total_items: number
  total_edges: number
  by_category: Record<string, number>
  by_source: Record<string, number>
  recent_items: { id: string; title: string; category: string; updated_at: string }[]
}

export interface KnowledgeSearchResult {
  item: KnowledgeItem
  snippet: string
  rank: number
}

export interface ProjectDocument {
  id: string
  project_id: string
  file_path: string
  file_name: string
  file_type: string
  file_size: number
  file_hash: string
  last_scanned_at: string | null
  scan_status: 'pending' | 'scanning' | 'done' | 'error'
  total_sections: number
  extracted_items: number
  created_at: string
  updated_at: string
}

export interface ProjectDocumentDetail extends ProjectDocument {
  knowledge_items: KnowledgeItem[]
}

export interface EdgeCreate {
  source_item_id: string
  target_item_id: string
  edge_type?: EdgeType
  label?: string | null
}

export interface SuggestedEdge {
  target_item_id: string
  target_title: string
  target_category: string
  edge_type: EdgeType
  reason: string
  confidence: number
}

export const CATEGORY_LABELS: Record<KnowledgeCategory, string> = {
  architecture: 'Architektur',
  business_logic: 'Geschäftslogik',
  infrastructure: 'Infrastruktur',
  process: 'Prozess',
  decision: 'Entscheidung',
  reference: 'Referenz',
  custom: 'Benutzerdefiniert',
}

export const CATEGORY_COLORS: Record<KnowledgeCategory, string> = {
  architecture: '#3b82f6',
  business_logic: '#10b981',
  infrastructure: '#f59e0b',
  process: '#8b5cf6',
  decision: '#ef4444',
  reference: '#6b7280',
  custom: '#06b6d4',
}

export const EDGE_TYPE_LABELS: Record<EdgeType, string> = {
  related: 'Verwandt',
  references: 'Referenziert',
  based_on: 'Basiert auf',
  extends: 'Erweitert',
}

export const SOURCE_TYPE_KB_LABELS: Record<KnowledgeSourceType, string> = {
  manual: 'Manuell',
  research: 'Recherche',
  note_import: 'Notiz-Import',
  email_extract: 'Email-Extrakt',
  chat_extract: 'Chat-Extrakt',
  confluence: 'Confluence',
  document: 'Dokument',
}

export const CONFIDENCE_LABELS: Record<Confidence, string> = {
  high: 'Hoch',
  medium: 'Mittel',
  low: 'Niedrig',
}
