import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Plotly 是最大的库（~3.5MB gzipped 前），单独拆包
          if (id.includes('node_modules/plotly.js')) {
            return 'plotly';
          }
          if (id.includes('node_modules/react-plotly.js')) {
            return 'plotly-react';
          }
          // React 生态核心
          if (
            id.includes('node_modules/react') ||
            id.includes('node_modules/react-dom') ||
            id.includes('node_modules/react-router-dom')
          ) {
            return 'vendor';
          }
        },
      },
    },
  },
});
