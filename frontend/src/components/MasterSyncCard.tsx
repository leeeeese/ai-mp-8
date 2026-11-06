import { GitCommitHorizontal } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useGraphStatus } from '@/api/client';
import { SM_LABEL } from '@/api/types';
import { cn, formatDateTime } from '@/lib/cn';
import { useUi } from '@/store/ui';

/** 적재 현황 · master 대비. 반영률 계산은 백엔드, 프론트는 표시만 */
export function MasterSyncCard({ compact }: { compact?: boolean }) {
  const sm = useUi((s) => s.graphSm);
  const { data } = useGraphStatus(sm);
  const m = data?.masterSync;
  const filled = m ? Math.round(m.percent / 5) : 0;
  return (
    <Link
      to="/graph#pending"
      className={cn('glass block p-3 transition-colors hover:border-[color:var(--accent)]', compact && 'p-2.5')}
      aria-label={m ? `${SM_LABEL[sm]} 적재 현황 master 대비 ${m.percent}% 반영, 적재 현황으로 이동` : '적재 현황으로 이동'}
    >
      <div className="flex items-center justify-between text-xs text-muted">
        <span>적재 현황 · master 대비</span>
        <span>{SM_LABEL[sm]}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className={cn('font-mono text-xl font-semibold', m && m.percent < 100 ? 'text-accent' : 'text-ok')}>{m ? m.percent : '–'}</span>
        <span className="text-xs text-muted">%</span>
      </div>
      <div className="mt-1.5 grid gap-[2px]" aria-hidden="true" style={{ gridTemplateColumns: 'repeat(20, minmax(0, 1fr))' }}>
        {Array.from({ length: 20 }, (_, i) => (
          <span key={i} className={cn('h-2 rounded-[2px]', i < filled ? 'bg-accent' : 'bg-[color:var(--border)]')} />
        ))}
      </div>
      {m && (
        <div className="mt-2 space-y-0.5 text-[11px] text-muted">
          <div className="flex items-center gap-1 font-mono">
            <GitCommitHorizontal size={12} /> {m.loadedCommit} → {m.masterCommit}
          </div>
          <div>
            {m.behindCommits ? `${m.behindCommits}커밋 뒤 · 미반영 파일 ${m.pendingFiles.length}개` : '최신 반영'} · {formatDateTime(m.loadedAt)}
          </div>
        </div>
      )}
    </Link>
  );
}
