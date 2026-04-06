import { defineConfig } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  server: {
    proxy: {
      '/api': 'http://mirroring:8000',
      '/ws': {
        target: 'ws://mirroring:8000',
        ws: true,
      },
      '/auth': 'http://auth-service:8003',
      '/provision': 'http://provisioning-server:8001',
    }
  }
})