import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import path from 'path';

// Build for the conversational UI (chat.html → served at /chat).
//
// It is built separately from the workspace app so the two pages never share
// chunks: the main build's hand-tuned vendor chunks assume the workspace
// entry's load order. Output goes to dist/chat/, so run this AFTER the main
// build (which empties dist/). `yarn build` does both in order.
const apiPort = process.env.API_PORT || 5567;

export default defineConfig({
  plugins: [react()],
  base: '/chat/',
  publicDir: false,
  resolve: { dedupe: ['vega', 'vega-lite', 'react', 'react-dom'] },
  build: {
    outDir: path.join(__dirname, 'py-src', 'data_formulator', 'dist', 'chat'),
    emptyOutDir: true,
    rollupOptions: { input: path.resolve(__dirname, 'chat.html') },
    chunkSizeWarningLimit: 2500,
  },
  server: {
    proxy: { '/api': { target: `http://localhost:${apiPort}`, changeOrigin: true } },
  },
});
