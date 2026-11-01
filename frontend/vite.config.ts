import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  // 固定端口：README 与团队约定都写的是 5199，别让 vite 随机换端口
  server: { port: 5199 },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // three.js 体积大且极少改动：单独成 chunk，发版后可长期命中浏览器缓存
        manualChunks: {
          three: ['three'],
        },
      },
    },
  },
});
