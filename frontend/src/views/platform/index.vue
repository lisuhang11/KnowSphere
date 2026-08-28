<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import { sessionId } from '@/api/sessions'
import { useChatStore } from '@/stores/chat'
import SessionSidebarRow from './components/SessionSidebarRow.vue'

const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()

const pinnedThreads = computed(() => chatStore.threads.filter((t) => t.is_pinned))
const regularThreads = computed(() => chatStore.threads.filter((t) => !t.is_pinned))

const menus = [
  { path: '/chat', title: '智能对话', icon: 'chat' },
  { path: '/knowledge-bases', title: '知识库', icon: 'folder-open' },
  { path: '/models', title: '模型管理', icon: 'setting' },
  { path: '/evaluation', title: '效果评测', icon: 'chart' },
]

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

async function newChat() {
  try {
    await chatStore.createChat()
    void router.push('/chat')
  } catch (e) {
    MessagePlugin.error(`创建会话失败: ${(e as Error).message}`)
  }
}

function openSession(id: string) {
  chatStore.selectThread(id)
  void router.push('/chat')
}

async function renameSession(id: string, title: string) {
  try {
    await chatStore.renameThread(id, title)
    MessagePlugin.success('会话已重命名')
  } catch {
    MessagePlugin.error('重命名失败')
  }
}

async function clearSession(id: string) {
  try {
    await chatStore.clearThreadMessages(id)
    MessagePlugin.success('消息已清空')
  } catch {
    MessagePlugin.error('清空消息失败')
  }
}

async function deleteSession(id: string) {
  try {
    await chatStore.removeThread(id)
    MessagePlugin.success('会话已删除')
    if (chatStore.currentThreadId === id) {
      void router.push('/chat')
    }
  } catch {
    MessagePlugin.error('删除会话失败')
  }
}

async function togglePinSession(id: string) {
  try {
    const wasPinned = chatStore.threads.find((x) => sessionId(x) === id)?.is_pinned
    await chatStore.togglePin(id)
    MessagePlugin.success(wasPinned ? '已取消置顶' : '已置顶')
  } catch {
    MessagePlugin.error('置顶操作失败')
  }
}

onMounted(() => {
  void chatStore.loadThreads()
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo-row">
        <div class="logo" @click="newChat">
          <span class="logo-mark">K</span>
          <span class="logo-text">KnowSphere</span>
        </div>
      </div>

      <div class="new-chat">
        <t-button block theme="default" variant="outline" @click="newChat">
          <template #icon><t-icon name="add" /></template>
          新建对话
        </t-button>
      </div>

      <nav class="menu">
        <div
          v-for="m in menus"
          :key="m.path"
          class="menu-item"
          :class="{ active: isActive(m.path) }"
          @click="router.push(m.path)"
        >
          <t-icon :name="m.icon" size="18px" />
          <span class="menu-title">{{ m.title }}</span>
        </div>
      </nav>

      <div class="sessions">
        <div v-if="chatStore.threads.length === 0" class="sessions-empty">暂无会话</div>

        <template v-if="pinnedThreads.length">
          <div class="sessions-title">置顶</div>
          <SessionSidebarRow
            v-for="t in pinnedThreads"
            :key="sessionId(t)"
            :item="{ id: sessionId(t), title: chatStore.titleOf(t), is_pinned: true }"
            :active="sessionId(t) === chatStore.currentThreadId"
            @navigate="openSession(sessionId(t))"
            @rename="renameSession(sessionId(t), $event)"
            @pin="togglePinSession(sessionId(t))"
            @clear="clearSession(sessionId(t))"
            @delete="deleteSession(sessionId(t))"
          />
        </template>

        <template v-if="regularThreads.length">
          <div class="sessions-title">{{ pinnedThreads.length ? '历史会话' : '历史会话' }}</div>
          <SessionSidebarRow
            v-for="t in regularThreads"
            :key="sessionId(t)"
            :item="{ id: sessionId(t), title: chatStore.titleOf(t), is_pinned: false }"
            :active="sessionId(t) === chatStore.currentThreadId"
            @navigate="openSession(sessionId(t))"
            @rename="renameSession(sessionId(t), $event)"
            @pin="togglePinSession(sessionId(t))"
            @clear="clearSession(sessionId(t))"
            @delete="deleteSession(sessionId(t))"
          />
        </template>
      </div>

      <div class="sidebar-footer">KnowSphere v0.1 · LangGraph</div>
    </aside>

    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100%;
}

.sidebar {
  width: var(--ks-sidebar-width);
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid var(--td-border-level-1-color);
  display: flex;
  flex-direction: column;
  padding: 8px 6px 6px;
  box-sizing: border-box;
  overflow: hidden;
}

.logo-row {
  height: 50px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding: 0 10px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}

.logo-mark {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--td-brand-color);
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-text {
  font-size: 17px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.new-chat {
  padding: 4px 6px 8px;
}

.menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 6px 8px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--td-text-color-primary);
  transition: background-color 0.2s ease;
}

.menu-item:hover {
  background: var(--td-gray-bg-color, #f5f5f5);
}

.menu-item.active {
  background: var(--td-brand-color-light);
  color: var(--td-brand-color);
}

.menu-title {
  font-size: 14px;
}

.sessions {
  flex: 1;
  overflow-y: auto;
  padding: 0 6px;
  min-height: 0;
}

.sessions-title {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  padding: 8px 10px 6px;
}

.sessions-empty {
  font-size: 13px;
  color: var(--td-text-color-placeholder);
  padding: 8px 10px;
}

.sidebar-footer {
  flex-shrink: 0;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  border-top: 1px solid var(--td-border-level-1-color);
}

.main {
  flex: 1;
  min-width: 0;
  height: 100%;
  overflow: hidden;
}
</style>
