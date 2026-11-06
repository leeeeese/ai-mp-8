import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import type { Confidence } from '@/api/types';

type Tone = 'accent' | 'ok' | 'est' | 'warn' | 'muted';
const TONE: Record<Tone, string> = {
  accent: 'text-accent bg-[color:var(--accent-soft)]',
  ok: 'text-ok bg-surface-2',
  est: 'text-est bg-surface-2',
  warn: 'text-warn bg-surface-2',
  muted: 'text-muted bg-surface-2',
};

export function Badge({ tone = 'muted', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-xs font-medium', TONE[tone], className)}>
      {children}
    </span>
  );
}

/** 근거 확인 / 추정 배지 */
export function ConfidenceBadge({ value }: { value: Confidence }) {
  return value === 'confirmed' ? <Badge tone="ok">● 확인</Badge> : <Badge tone="est">◐ 추정</Badge>;
}
