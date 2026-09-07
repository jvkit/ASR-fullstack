import Vant from 'vant'
import 'vant/lib/index.css'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import { createApp } from 'vue'

import App from './App.vue'

createApp(App).use(ElementPlus).use(Vant).mount('#app')
