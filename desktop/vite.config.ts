import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// pyproject.toml is the project's single version source; the desktop manifests
// are synced to it by scripts/sync-desktop-version.py.
function projectVersion(): string {
  const pyproject = readFileSync(new URL('../pyproject.toml', import.meta.url), 'utf8')
  const start = pyproject.indexOf('[project]')
  const match = start === -1 ? null : /^version = "([^"]+)"/m.exec(pyproject.slice(start))
  if (match === null) throw new Error('no version in [project] of pyproject.toml')
  return match[1]
}

// The Tauri shell serves the built assets; the dev server is only used with
// `tauri dev`, which reads devUrl below (src-tauri/tauri.conf.json).
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(projectVersion()),
  },
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
