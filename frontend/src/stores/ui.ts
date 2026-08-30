import { defineStore } from 'pinia'

export const useUIStore = defineStore('ui', {
  state: () => ({
    sidebarCollapsed: localStorage.getItem('sidebar_collapsed') === 'true',
  }),
  actions: {
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      localStorage.setItem('sidebar_collapsed', String(this.sidebarCollapsed))
    },
    collapseSidebar() {
      this.sidebarCollapsed = true
      localStorage.setItem('sidebar_collapsed', 'true')
    },
    expandSidebar() {
      this.sidebarCollapsed = false
      localStorage.setItem('sidebar_collapsed', 'false')
    },
  },
})
