import vue from '@vitejs/plugin-vue'
import fs from 'node:fs'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    https: {
      key: fs.readFileSync('certs/server.key'),
      cert: fs.readFileSync('certs/server.crt'),
    },
    proxy: {
      // 后端 API + WS 走代理，避免 CORS
      '/api': 'http://localhost:8010',
      '/ws': { target: 'ws://localhost:8010', ws: true },
      '/health': 'http://localhost:8010',
    },
  },
})
