import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import type { Citation } from '@/api/sessions'
import { useChatReferencesDrawer } from '@/composables/useChatReferencesDrawer'
import { citationsToReferences } from '@/utils/referenceSources'

export type CitationFloatState = {
  visible: boolean
  type: 'kb' | 'web'
  top: number
  left: number
  title: string
  content: string
  url: string
  loading: boolean
  error: string
}

export function useChatCitationPopover(
  rootRef: Ref<HTMLElement | null>,
  options: {
    getCitations: () => Citation[] | undefined
    messageId?: () => string | undefined
  },
) {
  const referencesDrawer = useChatReferencesDrawer()

  const float = ref<CitationFloatState>({
    visible: false,
    type: 'kb',
    top: 0,
    left: 0,
    title: '',
    content: '',
    url: '',
    loading: false,
    error: '',
  })

  let closeTimer: number | null = null

  const positionFor = (el: HTMLElement) => {
    const rect = el.getBoundingClientRect()
    float.value.top = rect.bottom + window.scrollY + 6
    float.value.left = Math.min(rect.left + window.scrollX, window.innerWidth - 340)
  }

  const openForCitation = (el: HTMLElement) => {
    const idx = Number(el.getAttribute('data-cite-index'))
    const citations = options.getCitations() || []
    const c = citations.find((x) => x.index === idx)
    if (!c) return
    float.value.type = 'kb'
    float.value.title = c.file_name || `来源 ${idx}`
    float.value.content = c.snippet || ''
    float.value.url = ''
    float.value.loading = false
    float.value.error = ''
    float.value.visible = true
    positionFor(el)

    const refs = citationsToReferences(citations)
    referencesDrawer?.setHighlight({ documentId: c.document_id, index: idx })
    referencesDrawer && (referencesDrawer.references.value = refs)
  }

  const cancelClose = () => {
    if (closeTimer) {
      window.clearTimeout(closeTimer)
      closeTimer = null
    }
  }

  const scheduleClose = () => {
    cancelClose()
    closeTimer = window.setTimeout(() => {
      float.value.visible = false
    }, 180)
  }

  const onMouseOver = (e: MouseEvent) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('sup.cite:not(.static)')
    if (!el || !rootRef.value?.contains(el)) return
    cancelClose()
    openForCitation(el)
  }

  const onMouseOut = (e: MouseEvent) => {
    const related = e.relatedTarget as Node | null
    if (related && (e.currentTarget as Node).contains(related)) return
    scheduleClose()
  }

  const onClick = (e: MouseEvent) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('sup.cite:not(.static)')
    if (!el || !rootRef.value?.contains(el)) return
    e.preventDefault()
    e.stopPropagation()
    const idx = Number(el.getAttribute('data-cite-index'))
    const citations = options.getCitations() || []
    const refs = citationsToReferences(citations)
    const c = citations.find((x) => x.index === idx)
    referencesDrawer?.open({
      references: refs,
      highlight: { documentId: c?.document_id, index: idx },
      messageId: options.messageId?.() || '',
    })
  }

  const bind = () => {
    const root = rootRef.value
    if (!root) return
    root.addEventListener('mouseover', onMouseOver)
    root.addEventListener('mouseout', onMouseOut)
    root.addEventListener('click', onClick)
  }

  const unbind = () => {
    const root = rootRef.value
    if (!root) return
    root.removeEventListener('mouseover', onMouseOver)
    root.removeEventListener('mouseout', onMouseOut)
    root.removeEventListener('click', onClick)
  }

  onMounted(bind)
  onBeforeUnmount(unbind)

  return { float, cancelClose, scheduleClose, rebind: () => { unbind(); bind() } }
}
