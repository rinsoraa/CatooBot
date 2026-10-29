/** 入口：Pinia 先于 Router（守卫会用到 auth store）。 */

import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'

import './styles/tokens.css'
import './styles/base.css'
import './styles/theme.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
