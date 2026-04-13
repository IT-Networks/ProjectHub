import { create } from 'zustand'
import { api } from '@/lib/api'
import type { ProjectListItem, Project, ProjectCreate, ProjectUpdate, DataSourceLinkCreate } from '@/lib/types'

interface ProjectStore {
  projects: ProjectListItem[]
  currentProject: Project | null
  loading: boolean
  error: string | null

  fetchProjects: () => Promise<void>
  fetchProject: (id: string) => Promise<void>
  createProject: (data: ProjectCreate) => Promise<Project>
  updateProject: (id: string, data: ProjectUpdate) => Promise<void>
  deleteProject: (id: string) => Promise<void>
  addSource: (projectId: string, data: DataSourceLinkCreate) => Promise<void>
  removeSource: (projectId: string, sourceId: string) => Promise<void>
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  projects: [],
  currentProject: null,
  loading: false,
  error: null,

  fetchProjects: async () => {
    set({ loading: true, error: null })
    try {
      const projects = await api.get<ProjectListItem[]>('/projects')
      set({ projects, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  fetchProject: async (id: string) => {
    set({ loading: true, error: null })
    try {
      const project = await api.get<Project>(`/projects/${id}`)
      set({ currentProject: project, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  createProject: async (data: ProjectCreate) => {
    const project = await api.post<Project>('/projects', data)
    await get().fetchProjects()
    return project
  },

  updateProject: async (id: string, data: ProjectUpdate) => {
    const updated = await api.put<Project>(`/projects/${id}`, data)
    set({ currentProject: updated })
    await get().fetchProjects()
  },

  deleteProject: async (id: string) => {
    await api.del(`/projects/${id}`)
    set({ currentProject: null })
    await get().fetchProjects()
  },

  addSource: async (projectId: string, data: DataSourceLinkCreate) => {
    await api.post(`/projects/${projectId}/sources`, data)
    await get().fetchProject(projectId)
  },

  removeSource: async (projectId: string, sourceId: string) => {
    await api.del(`/projects/${projectId}/sources/${sourceId}`)
    await get().fetchProject(projectId)
  },
}))
