import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': '/src' } },
  // VITE_USE_MOCK=false 일 때 /api(SSE 포함)를 FastAPI(uvicorn, 8000)로 보낸다. mock 모드에서는 MSW 가 먼저 가로채 쓰이지 않음
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000' } },
});
