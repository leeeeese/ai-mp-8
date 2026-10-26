import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/store/theme';

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const dark = theme === 'dark';
  return (
    <button type="button" className="icon-btn" onClick={toggle} aria-label={dark ? '라이트 모드로 전환' : '다크 모드로 전환'}>
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
