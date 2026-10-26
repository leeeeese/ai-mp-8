import { Check, Copy } from 'lucide-react';
import { useState } from 'react';

/** 확인 쿼리 (읽기 전용, 복사만. 실행은 사용자가 직접) */
export function QueryBlock({ title, sql }: { title: string; sql: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 클립보드 권한 없음 */
    }
  };
  return (
    <figure className="overflow-hidden rounded-chip border border-border bg-surface-2">
      <figcaption className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted">
        <span>{title} · 읽기 전용</span>
        <button type="button" onClick={copy} className="inline-flex min-h-[32px] items-center gap-1 rounded-full px-2 hover:text-text" aria-label={`${title} 쿼리 복사`}>
          {copied ? <Check size={14} className="text-ok" /> : <Copy size={14} />} {copied ? '복사됨' : '복사'}
        </button>
      </figcaption>
      <pre className="overflow-x-auto p-3 font-mono text-[12px] leading-relaxed text-text">{sql}</pre>
    </figure>
  );
}
