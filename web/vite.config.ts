import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api、/ws 代理到本机 api 容器（docker-compose.dev.yml 暴露 18000）
const API = 'http://127.0.0.1:18000'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': API,
      '/docs': API,
      '/openapi.json': API,
      '/ws': { target: API, ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1000,
  },
})
