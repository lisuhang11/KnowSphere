/** Markdown 渲染封装：marked + highlight.js 代码高亮 + DOMPurify 消毒 */
import DOMPurify from 'dompurify'
import hljs from 'highlight.js'
import { marked } from 'marked'
import 'highlight.js/styles/github.css'

const renderer = new marked.Renderer()

renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
  const language = (lang || '').split(/\s+/)[0]
  const html =
    language && hljs.getLanguage(language)
      ? hljs.highlight(text, { language }).value
      : hljs.highlightAuto(text).value
  return `<pre><code class="hljs language-${language || 'plaintext'}">${html}</code></pre>`
}

marked.use({ gfm: true, breaks: true, renderer })

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text) as string)
}

// 引用句柄 [[cN]]（checkpoint 中持久化的 LLM 原文格式）
const HISTORY_CITE_RE = /\[\[c(\d{1,3})\]\]/g

/**
 * 历史消息还原：实时流中句柄已由后端展开为 <sup class="cite">，
 * 而历史（checkpoint 原文）仍是 [[cN]]，这里渲染为无元数据的静态角标，
 * 保持视觉一致；static 角标不可点击（没有 citation_meta 数据）。
 */
export function renderHistoryContent(text: string): string {
  return renderMarkdown(text.replace(HISTORY_CITE_RE, '<sup class="cite static">$1</sup>'))
}
