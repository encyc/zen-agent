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

// Without an updater public key in the Tauri config the shell cannot verify a
// release, so the update UI stays dark: no check, no banner, no dead button.
function updaterEnabled(): boolean {
  const conf = readFileSync(new URL('./src-tauri/tauri.conf.json', import.meta.url), 'utf8')
  const updater = JSON.parse(conf).plugins?.updater
  return typeof updater?.pubkey === 'string' && updater.pubkey.trim() !== ''
}

// The Tauri shell serves the built assets; the dev server is only used with
// `tauri dev`, which reads devUrl below (src-tauri/tauri.conf.json).
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(projectVersion()),
    __UPDATE_ENABLED__: JSON.stringify(updaterEnabled()),
  },
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    // Cargo rewrites src-tauri/target on every build; watching it restarts the
    // dev server mid-`tauri dev` for no frontend change.
    watch: { ignored: ['**/src-tauri/**'] },
  },
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
  },
})
