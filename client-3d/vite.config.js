import { defineConfig } from 'vite';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root,
  base: '/client-3d/',
  publicDir: false,
  build: {
    outDir: resolve(root, '../webapp/static/client-3d'),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(root, 'src/main.js'),
      output: {
        entryFileNames: 'client-3d.js',
        chunkFileNames: 'chunks/[name].js',
        assetFileNames: 'assets/client-3d[extname]',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/socket.io': { target: 'http://127.0.0.1:5001', ws: true },
      '/map_state': 'http://127.0.0.1:5001',
      '/action': 'http://127.0.0.1:5001',
      '/actions': 'http://127.0.0.1:5001',
      '/path': 'http://127.0.0.1:5001',
      '/target': 'http://127.0.0.1:5001',
      '/assets': 'http://127.0.0.1:5001',
      '/login': 'http://127.0.0.1:5001',
      '/logout': 'http://127.0.0.1:5001',
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
  },
});
