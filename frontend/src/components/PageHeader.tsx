import { Menu } from 'lucide-react';
import type { ReactNode } from 'react';
import { useUi } from '@/store/ui';
import { ThemeToggle } from './ThemeToggle';

/** 본문 상단 바: 제목 · 배지 · 우측 액션 + 테마 토글 (모바일은 사이드바 열기 버튼) */
export function PageHeader({ title, badges, actions }: { title: ReactNode; badges?: ReactNode; actions?: ReactNode }) {
  const setDrawer = useUi((s) => s.setDrawer);
  return (
    <header className="sticky top-0 z-20 flex min-h-[60px] flex-wrap items-center gap-x-3 gap-y-1 py-2 border-b border-border bg-[color:var(--bg)]/70 px-4 backdrop-blur-md md:px-6">
      <button type="button" className="icon-btn lg:hidden" onClick={() => setDrawer(true)} aria-label="메뉴 열기">
        <Menu size={18} />
      </button>
      <h1 className="min-w-0 truncate text-[15px] font-semibold">{title}</h1>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">{badges}</div>
      <div className="ml-auto flex items-center gap-1">
        {actions}
        <ThemeToggle />
      </div>
    </header>
  );
}
