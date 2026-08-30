import { createApp } from 'vue'
import { createPinia } from 'pinia'
import TDesign from 'tdesign-vue-next'
import 'tdesign-vue-next/es/style/index.css'
import '@/assets/theme/theme.css'
import '@/assets/dropdown-menu.less'
import '@/assets/app.css'
import '@/components/css/chat-hljs-dark.less'
import App from './App.vue'
import router from './router'

createApp(App).use(createPinia()).use(router).use(TDesign).mount('#app')
