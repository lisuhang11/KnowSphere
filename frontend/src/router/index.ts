import { createRouter, createWebHistory } from 'vue-router'
import PlatformLayout from '@/views/platform/index.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: PlatformLayout,
      redirect: '/chat',
      children: [
        {
          path: 'chat',
          name: 'chat',
          component: () => import('@/views/chat/index.vue'),
        },
        {
          path: 'knowledge-bases',
          name: 'knowledge-bases',
          component: () => import('@/views/knowledge-bases/index.vue'),
        },
        {
          path: 'knowledge-bases/:kbId',
          name: 'knowledge-base-detail',
          component: () => import('@/views/knowledge-bases/detail.vue'),
        },
        {
          path: 'models',
          name: 'models',
          component: () => import('@/views/models/index.vue'),
        },
        {
          path: 'agents',
          name: 'agents',
          component: () => import('@/views/agents/index.vue'),
        },
        {
          path: 'tools',
          name: 'tools',
          component: () => import('@/views/tools/index.vue'),
        },
        {
          path: 'skills',
          name: 'skills',
          component: () => import('@/views/skills/index.vue'),
        },
        {
          path: 'evaluation',
          name: 'evaluation',
          component: () => import('@/views/evaluation/index.vue'),
        },
        {
          path: 'documents/:documentId',
          name: 'document-detail',
          component: () => import('@/views/documents/detail.vue'),
        },
      ],
    },
  ],
})

export default router
