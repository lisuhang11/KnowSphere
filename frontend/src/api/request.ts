import axios, { type AxiosError } from 'axios'
import { MessagePlugin } from 'tdesign-vue-next'

const request = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

function formatAxiosError(error: AxiosError): string {
  if (!error.response) {
    return '无法连接后端，请确认 API 已在 8000 端口运行'
  }
  const payload = error.response.data as { detail?: unknown } | string | undefined
  const detail = payload && typeof payload === 'object' ? payload.detail : undefined
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) =>
        item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : '',
      )
      .filter(Boolean)
    if (msgs.length) return msgs.join('；')
  }
  if (error.response.status === 500) {
    return '后端内部错误（500）。请确认数据库与 API 已启动'
  }
  return error.message || '请求失败'
}

request.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error)) {
      MessagePlugin.error(formatAxiosError(error))
    } else {
      MessagePlugin.error((error as Error)?.message || '请求失败')
    }
    return Promise.reject(error)
  },
)

export default request
