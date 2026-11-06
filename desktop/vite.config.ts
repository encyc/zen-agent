import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Tauri shell serves the built assets; the dev server is only used with
// `tauri dev`, which reads devUrl below (src-tauri/tauri.conf.json).
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
  },
})
