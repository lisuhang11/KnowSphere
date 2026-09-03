<template>
  <div class="aside_box" :class="{ 'aside_box--collapsed': uiStore.sidebarCollapsed }">
    <div class="logo_row" v-if="!uiStore.sidebarCollapsed">
      <div class="logo_box" @click="router.push('/knowledge-bases')">
        <span class="logo-mark">K</span>
        <span class="logo-text">KnowSphere</span>
      </div>
      <div class="logo_actions">
        <div class="sidebar-toggle" title="收起侧栏" @click="uiStore.toggleSidebar">
          <svg viewBox="0 0 20 20" width="18" height="18" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="1.5" y="1.5" width="17" height="17" rx="3" stroke="currentColor" stroke-width="1.2" />
            <line x1="7.5" y1="1.5" x2="7.5" y2="18.5" stroke="currentColor" stroke-width="1.2" />
            <line x1="4" y1="7.5" x2="4" y2="12.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
          </svg>
        </div>
      </div>
    </div>
    <t-tooltip v-else content="展开侧栏" placement="right">
      <div class="menu_item sidebar-toggle-item" @click="uiStore.toggleSidebar">
        <div class="menu_item-box">
          <div class="menu_icon">
            <svg class="icon" viewBox="0 0 20 20" width="20" height="20" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="1.5" y="1.5" width="17" height="17" rx="3" stroke="currentColor" stroke-width="1.2" />
              <line x1="7.5" y1="1.5" x2="7.5" y2="18.5" stroke="currentColor" stroke-width="1.2" />
              <line x1="5" y1="10" x2="3" y2="8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
              <line x1="5" y1="10" x2="3" y2="12" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
            </svg>
          </div>
        </div>
      </div>
    </t-tooltip>

    <div v-if="uiStore.sidebarCollapsed" class="sidebar-drag-handle" @mousedown="onDragHandleMouseDown" />

    <div class="menu_top">
      <div
        class="menu_box"
        :class="{ 'menu_box--sticky': item.children && !uiStore.sidebarCollapsed }"
        v-for="item in topMenuItems"
        :key="item.path"
      >
        <t-tooltip :content="item.title" placement="right" :disabled="!uiStore.sidebarCollapsed">
          <div
            @click="handleMenuClick(item.path)"
            :class="[
              'menu_item',
              isMenuItemActive(item.path) ? 'menu_item_active' : '',
            ]"
          >
            <div class="menu_item-box">
              <div class="menu_icon">
                <img class="icon" :src="getImgSrc(iconFile(item.path))" alt="" />
              </div>
              <span v-if="!uiStore.sidebarCollapsed" class="menu_title" :title="item.title">{{ item.title }}</span>
            </div>
          </div>
        </t-tooltip>
      </div>

      <div class="submenu" v-if="!uiStore.sidebarCollapsed">
        <div v-if="chatStore.threads.length === 0" class="submenu_empty">暂无会话</div>
        <div v-else class="session-filtered-list">
          <template v-for="group in groupedSessions" :key="group.key">
            <div v-if="group.label" class="timeline_header session-list-row session-list-row--flat">
              <span class="session-list-row__body">
                <span class="timeline_header-label">{{ group.label }}</span>
              </span>
            </div>
            <div
              v-for="subitem in group.items"
              :key="subitem.id"
              class="submenu_item_p session-chat-row"
              :class="{ 'session-chat-row--active': subitem.path === currentSecondpath }"
            >
              <div class="session-list-row session-list-row--flat">
                <div class="session-list-row__body">
                  <SessionSidebarRow
                    :item="subitem"
                    :active-path="currentSecondpath"
                    :menu-options="buildSessionMenuOptions(subitem)"
                    @navigate="openSession(subitem.id)"
                    @menu-click="handleSessionMenuClick($event, subitem)"
                    @rename-submit="renameSessionTitle(subitem, $event.title)"
                  />
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <div class="menu_bottom">
      <div class="sidebar-footer">KnowSphere v0.1</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Icon as TIcon, MessagePlugin } from 'tdesign-vue-next'
import { sessionId } from '@/api/sessions'
import { useChatStore } from '@/stores/chat'
import { useUIStore } from '@/stores/ui'
import SessionSidebarRow from '@/components/SessionSidebarRow.vue'
import { classifyDateBucket, groupSessionsByDate, type DateBucketKey } from '@/components/sessionGrouping'
import prefixIcon from '@/assets/img/prefixIcon.svg'
import prefixIconGreen from '@/assets/img/prefixIcon-green.svg'
import zhishiku from '@/assets/img/zhishiku.svg'
import zhishikuGreen from '@/assets/img/zhishiku-green.svg'
import setting from '@/assets/img/setting.svg'
import settingGreen from '@/assets/img/setting-green.svg'
import agentIcon from '@/assets/img/agent.svg'
import agentIconGreen from '@/assets/img/agent-green.svg'
import toolsIcon from '@/assets/img/tools.svg'
import toolsIconGreen from '@/assets/img/tools-green.svg'
import skillIcon from '@/assets/img/skill.svg'
import skillIconGreen from '@/assets/img/skill-green.svg'
import evalIcon from '@/assets/img/eval.svg'
import evalIconGreen from '@/assets/img/eval-green.svg'

const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()
const uiStore = useUIStore()

type MenuItem = { title: string; path: string; children?: boolean }

const ICON_MAP: Record<string, string> = {
  'prefixIcon.svg': prefixIcon,
  'prefixIcon-green.svg': prefixIconGreen,
  'zhishiku.svg': zhishiku,
  'zhishiku-green.svg': zhishikuGreen,
  'setting.svg': setting,
  'setting-green.svg': settingGreen,
  'agent.svg': agentIcon,
  'agent-green.svg': agentIconGreen,
  'tools.svg': toolsIcon,
  'tools-green.svg': toolsIconGreen,
  'skill.svg': skillIcon,
  'skill-green.svg': skillIconGreen,
  'eval.svg': evalIcon,
  'eval-green.svg': evalIconGreen,
}

const topMenuItems: MenuItem[] = [
  { title: '新对话', path: 'creatChat', children: true },
  { title: '知识库', path: 'knowledge-bases' },
  { title: '智能体', path: 'agents' },
  { title: '工具', path: 'tools' },
  { title: '技能', path: 'skills' },
  { title: '模型管理', path: 'models' },
  { title: '效果评测', path: 'evaluation' },
]

const dateBucketLabels: Record<DateBucketKey, string> = {
  pinned: '置顶',
  today: '今天',
  yesterday: '昨天',
  last7Days: '近 7 天',
  last30Days: '近 30 天',
  lastYear: '近一年',
  earlier: '更早',
}

const currentSecondpath = computed(() =>
  chatStore.currentThreadId && route.path.startsWith('/chat') ? `chat/${chatStore.currentThreadId}` : '',
)

const groupedSessions = computed(() => {
  type SidebarSession = {
    id: string
    title: string
    is_pinned?: boolean
    created_at?: string
    updated_at?: string
    path: string
  }
  const items: SidebarSession[] = chatStore.threads.map((t) => ({
    id: sessionId(t),
    title: chatStore.titleOf(t),
    is_pinned: t.is_pinned,
    created_at: t.created_at,
    updated_at: t.updated_at,
    path: `chat/${sessionId(t)}`,
  }))
  return groupSessionsByDate(
    items,
    dateBucketLabels,
    (session) => classifyDateBucket(session.updated_at || session.created_at),
  )
})

function isMenuItemActive(itemPath: string): boolean {
  const currentRoute = route.name
  switch (itemPath) {
    case 'knowledge-bases':
      return currentRoute === 'knowledge-bases' || currentRoute === 'knowledge-base-detail'
    case 'agents':
      return currentRoute === 'agents'
    case 'tools':
      return currentRoute === 'tools'
    case 'skills':
      return currentRoute === 'skills'
    case 'models':
      return currentRoute === 'models'
    case 'evaluation':
      return currentRoute === 'evaluation'
    case 'creatChat':
      return currentRoute === 'chat' && !chatStore.currentThreadId
    default:
      return false
  }
}

function iconFile(path: string): string {
  if (path === 'knowledge-bases') {
    return isMenuItemActive(path) ? 'zhishiku-green.svg' : 'zhishiku.svg'
  }
  if (path === 'agents') {
    return isMenuItemActive(path) ? 'agent-green.svg' : 'agent.svg'
  }
  if (path === 'tools') {
    return isMenuItemActive(path) ? 'tools-green.svg' : 'tools.svg'
  }
  if (path === 'skills') {
    return isMenuItemActive(path) ? 'skill-green.svg' : 'skill.svg'
  }
  if (path === 'models') {
    return isMenuItemActive(path) ? 'setting-green.svg' : 'setting.svg'
  }
  if (path === 'evaluation') {
    return isMenuItemActive(path) ? 'eval-green.svg' : 'eval.svg'
  }
  return isMenuItemActive(path) ? 'prefixIcon-green.svg' : 'prefixIcon.svg'
}

function getImgSrc(url: string): string {
  return ICON_MAP[url] || ''
}

async function handleMenuClick(path: string) {
  if (path === 'creatChat') {
    chatStore.startDraftChat()
    void router.push('/chat')
    return
  }
  if (path === 'knowledge-bases') {
    void router.push('/knowledge-bases')
    return
  }
  void router.push(`/${path}`)
}

function openSession(id: string) {
  chatStore.selectThread(id)
  void router.push('/chat')
}

function buildSessionMenuOptions(item: { is_pinned?: boolean }) {
  const options: any[] = []
  if (item.is_pinned) {
    options.push({
      content: '取消置顶',
      value: 'unpin',
      prefixIcon: () => h(TIcon, { name: 'pin-filled', size: '16px' }),
    })
  } else {
    options.push({
      content: '置顶',
      value: 'pin',
      prefixIcon: () => h(TIcon, { name: 'pin', size: '16px' }),
    })
  }
  options.push(
    { content: '重命名', value: 'rename', prefixIcon: () => h(TIcon, { name: 'edit-1', size: '16px' }) },
    { content: '清空消息', value: 'clearMessages', prefixIcon: () => h(TIcon, { name: 'clear', size: '16px' }) },
    { content: '删除会话', value: 'delete', theme: 'error', prefixIcon: () => h(TIcon, { name: 'delete', size: '16px' }) },
  )
  return options
}

async function handleSessionMenuClick(data: { value: string }, item: { id: string }) {
  if (data?.value === 'delete') {
    try {
      const wasCurrent = chatStore.currentThreadId === item.id
      await chatStore.removeThread(item.id)
      MessagePlugin.success('会话已删除')
      if (wasCurrent) chatStore.startDraftChat()
    } catch {
      /* axios 拦截器已提示 */
    }
  } else if (data?.value === 'clearMessages') {
    try {
      await chatStore.clearThreadMessages(item.id)
      MessagePlugin.success('消息已清空')
    } catch {
      /* axios 拦截器已提示 */
    }
  } else if (data?.value === 'pin' || data?.value === 'unpin') {
    try {
      await chatStore.togglePin(item.id)
      MessagePlugin.success(data.value === 'pin' ? '已置顶' : '已取消置顶')
    } catch {
      /* axios 拦截器已提示 */
    }
  }
}

async function renameSessionTitle(item: { id: string }, title: string) {
  try {
    await chatStore.renameThread(item.id, title)
    MessagePlugin.success('会话已重命名')
  } catch {
    /* axios 拦截器已提示 */
  }
}

function onDragHandleMouseDown(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const expandThreshold = 40
  const onMouseMove = (ev: MouseEvent) => {
    if (ev.clientX - startX > expandThreshold) {
      uiStore.expandSidebar()
      cleanup()
    }
  }
  const onMouseUp = () => cleanup()
  const cleanup = () => {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

onMounted(() => {
  void chatStore.loadThreads().catch(() => {
    /* axios 拦截器已提示，避免未处理的 Promise */
  })
})
</script>

<style lang="less" scoped>
@import './menu-aside.less';

.logo-mark {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--td-brand-color);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-right: 8px;
}

.logo-text {
  font-size: 16px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
}

.sidebar-footer {
  flex-shrink: 0;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}
</style>

<style lang="less">
@import './menu-aside-global.less';
</style>
