import { cn } from '@/lib/cn';

/** 빛나는 궤도 로고 타일 (홈 빈 상태·사이드바) */
export function OrbitLogo({ size = 'lg' }: { size?: 'sm' | 'lg' }) {
  const box = size === 'lg' ? 'h-24 w-24 rounded-[24px]' : 'h-9 w-9 rounded-[10px]';
  const icon = size === 'lg' ? 64 : 22;
  return (
    <div className={cn('glass relative flex items-center justify-center shadow-glow', box)} aria-hidden="true">
      <svg width={icon} height={icon} viewBox="0 0 64 64" fill="none">
        <defs>
          <linearGradient id="orbit" x1="0" y1="0" x2="64" y2="64">
            <stop offset="0" stopColor="var(--accent)" />
            <stop offset="1" stopColor="var(--text)" stopOpacity="0.6" />
          </linearGradient>
        </defs>
        <ellipse cx="32" cy="32" rx="27" ry="12" stroke="url(#orbit)" strokeWidth="2.4" transform="rotate(35 32 32)" />
        <ellipse cx="32" cy="32" rx="27" ry="12" stroke="url(#orbit)" strokeWidth="2.4" transform="rotate(-35 32 32)" />
        <path d="M32 22 L34 30 L42 32 L34 34 L32 42 L30 34 L22 32 L30 30 Z" fill="var(--accent)" />
      </svg>
    </div>
  );
}
