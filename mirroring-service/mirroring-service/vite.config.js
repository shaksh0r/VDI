import { defineConfig } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

const MIRRORING_HOST = process.env.VITE_MIRRORING_HOST || 'localhost'
const AUTH_HOST = process.env.VITE_AUTH_HOST || 'localhost'
const PROVISION_HOST = process.env.VITE_PROVISION_HOST || 'localhost'

const httpTarget = (host, port) => `http://${host}:${port}`
const wsTarget = (host, port) => `ws://${host}:${port}`

export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  server: {
    proxy: {
      '/api': httpTarget(MIRRORING_HOST, 8000),
      '/ws': {
        target: wsTarget(MIRRORING_HOST, 8000),
        ws: true,
      },
      '/auth': httpTarget(AUTH_HOST, 8003),
      '/provision': httpTarget(PROVISION_HOST, 8001),
    }
  }
})