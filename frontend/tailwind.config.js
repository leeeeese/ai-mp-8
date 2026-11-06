/** @type {import('tailwindcss').Config} 색은 모두 CSS 변수(테마 토큰)로 연결 — src/styles/index.css */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        border: 'var(--border)',
        text: 'var(--text)',
        muted: 'var(--muted)',
        accent: 'var(--accent)',
        'accent-fg': 'var(--accent-fg)',
        ok: 'var(--ok)',
        est: 'var(--est)',
        warn: 'var(--warn)',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        sans: ['Inter', '"Noto Sans KR"', 'system-ui', 'sans-serif'],
      },
      borderRadius: { card: '16px', chip: '8px' },
      boxShadow: { glow: 'var(--accent-glow)' },
    },
  },
  plugins: [],
};
