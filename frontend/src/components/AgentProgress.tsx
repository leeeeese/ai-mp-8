import { Check, Circle, Loader2, MinusCircle } from 'lucide-react';
import type { AgentCounters, AgentEvent, AgentNode, NodeStatus } from '@/api/types';
import { LIMITS } from '@/config';
import { cn, formatMs } from '@/lib/cn';

export const NODES: { node: AgentNode; label: string; loop?: boolean }[] = [
  { node: 'supervisor', label: 'Supervisor' },
  { node: 'planner', label: '1 Planner' },
  { node: 'explorer', label: '2-1 Explorer', loop: true },
  { node: 'retriever', label: '2-2 Retriever', loop: true },
  { node: 'simulator', label: '2-3 Simulator' },
  { node: 'verifier', label: '2-4 Verifier' },
  { node: 'kpi', label: 'KPI Agent' },
  { node: 'composer', label: '3 Composer' },
];

export interface ProgressState {
  status: Record<AgentNode, NodeStatus>;
  detail: Partial<Record<AgentNode, string>>;
  elapsed: Partial<Record<AgentNode, number>>;
  counters: AgentCounters;
  evidence: NonNullable<AgentEvent['evidence']>;
}

export const emptyProgress = (): ProgressState => ({
  status: Object.fromEntries(NODES.map((n) => [n.node, 'waiting'])) as ProgressState['status'],
  detail: {},
  elapsed: {},
  counters: { toolCalls: 0, exploreLoops: 0, replans: 0 },
  evidence: [],
});

export function reduceProgress(s: ProgressState, e: AgentEvent): ProgressState {
  return {
    status: { ...s.status, [e.node]: e.status },
    detail: e.detail ? { ...s.detail, [e.node]: e.detail } : s.detail,
    elapsed: e.elapsedMs !== undefined ? { ...s.elapsed, [e.node]: e.elapsedMs } : s.elapsed,
    counters: e.counters ?? s.counters,
    evidence: e.evidence ? [...s.evidence, ...e.evidence] : s.evidence,
  };
}

const ICON: Record<NodeStatus, JSX.Element> = {
  waiting: <Circle size={14} className="text-muted" />,
  running: <Loader2 size={14} className="animate-spin text-accent" />,
  done: <Check size={14} className="text-ok" />,
  skipped: <MinusCircle size={14} className="text-muted" />,
};
const STATUS_LABEL: Record<NodeStatus, string> = { waiting: '대기', running: '진행', done: '완료', skipped: '생략' };

function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  return (
    <div>
      <div className="flex justify-between text-[11px] text-muted">
        <span>{label}</span>
        <span className="font-mono">
          {value}/{max}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label={label} aria-valuenow={value} aria-valuemax={max}>
        <div className={cn('h-full rounded-full', value >= max ? 'bg-warn' : 'bg-accent')} style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
      </div>
    </div>
  );
}

/** 우측 "Agent 진행" 패널 (SSE 로 실시간 갱신) */
export function AgentProgress({ state }: { state: ProgressState }) {
  return (
    <section aria-label="Agent 진행" aria-live="polite" className="glass flex flex-col gap-4 p-4">
      <h2 className="text-sm font-semibold">Agent 진행</h2>
      <ol className="flex flex-col">
        {NODES.map((n, i) => {
          const st = state.status[n.node];
          return (
            <li key={n.node} className="relative flex gap-3 pb-3 last:pb-0">
              {i < NODES.length - 1 && <span className="absolute left-[11px] top-6 h-[calc(100%-18px)] w-px bg-[color:var(--border)]" aria-hidden="true" />}
              <span className={cn('z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border bg-surface', st === 'running' && 'running-glow')}>
                {ICON[st]}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[13px]">
                  <span className={cn('font-mono', st === 'waiting' || st === 'skipped' ? 'text-muted' : 'text-text')}>{n.label}</span>
                  {n.loop && <span className="text-[10px] text-muted">⇄</span>}
                  <span className="ml-auto text-[11px] text-muted">
                    {STATUS_LABEL[st]}
                    {state.elapsed[n.node] !== undefined && ` · ${formatMs(state.elapsed[n.node]!)}`}
                  </span>
                </div>
                {state.detail[n.node] && <p className="mt-0.5 truncate text-xs text-muted" title={state.detail[n.node]}>{state.detail[n.node]}</p>}
              </div>
            </li>
          );
        })}
      </ol>
      <div className="flex flex-col gap-2 border-t border-border pt-3">
        <Meter label="도구 호출" value={state.counters.toolCalls} max={LIMITS.maxToolCalls} />
        <Meter label="Explorer ⇄ Retriever 왕복" value={state.counters.exploreLoops} max={LIMITS.maxExploreLoop} />
        <Meter label="재계획" value={state.counters.replans} max={LIMITS.maxReplan} />
      </div>
      {state.evidence.length > 0 && (
        <div className="border-t border-border pt-3">
          <h3 className="mb-1.5 text-xs font-medium text-muted">근거</h3>
          <ul className="flex flex-col gap-1.5">
            {state.evidence.map((e) => (
              <li key={e.nodeId + e.file} className="text-xs">
                <div className="node-id truncate text-text">{e.nodeId}</div>
                <div className="truncate text-muted">
                  {e.file}
                  {e.location && ` · ${e.location}`}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
