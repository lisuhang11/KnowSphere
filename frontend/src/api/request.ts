import axios from 'axios'
import { MessagePlugin } from 'tdesign-vue-next'

const request = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

request.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error.response?.data?.detail
    MessagePlugin.error(detail || error.message || '请求失败')
    return Promise.reject(error)
  },
)

export default request
