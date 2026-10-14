import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 開発時: 画面は 5190、バックエンドは 8200（/api を転送）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5190,
    proxy: { '/api': { target: 'http://127.0.0.1:8200', changeOrigin: true } },
  },
})
