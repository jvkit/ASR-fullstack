import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 后端 API + WS 走代理，避免 CORS（dev 模式）
      '/api': 'http://localhost:8010',
      '/ws': { target: 'ws://localhost:8010', ws: true },
      '/health': 'http://localhost:8010',
    },
  },
})
