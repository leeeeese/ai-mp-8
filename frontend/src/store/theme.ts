import { create } from 'zustand';

type Theme = 'light' | 'dark';
const KEY = 'impact-theme';

function initial(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === 'dark' ? 'dark' : 'light';
}

export const useTheme = create<{ theme: Theme; toggle: () => void }>((set, get) => ({
  theme: initial(),
  toggle: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* 저장 불가 환경은 무시 */
    }
    set({ theme: next });
  },
}));
