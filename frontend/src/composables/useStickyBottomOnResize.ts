import { onMounted, onUnmounted, type Ref } from 'vue'

/**
 * 消息列表高度变化时（markdown / 图片渲染完成）保持贴底。
 * 用户已经往上翻时不强制拉回去。
 */
export function useStickyBottomOnResize(
  scrollContainer: Ref<HTMLElement | null | undefined>,
  userHasScrolledUp: Ref<boolean>,
  scrollToBottom: () => void,
): void {
  let resizeObserver: ResizeObserver | null = null
  let scrollFrame: number | null = null

  const scheduleFollow = () => {
    if (userHasScrolledUp.value || scrollFrame !== null) return
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = null
      if (!userHasScrolledUp.value) scrollToBottom()
    })
  }

  onMounted(() => {
    const messageList = scrollContainer.value?.firstElementChild
    if (!messageList || typeof ResizeObserver === 'undefined') return
    resizeObserver = new ResizeObserver(scheduleFollow)
    resizeObserver.observe(messageList)
  })

  onUnmounted(() => {
    resizeObserver?.disconnect()
    resizeObserver = null
    if (scrollFrame !== null) {
      cancelAnimationFrame(scrollFrame)
      scrollFrame = null
    }
  })
}
