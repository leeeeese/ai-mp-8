import { useEffect, useMemo, useRef } from 'react';
import { useSessionLogs } from '@/api/client';
import type { SessionLog } from '@/api/types';
import { Modal } from './Modal';

interface Block {
  title: string;
  footer?: string;
  rows: SessionLog[];
}
type Item = { log: SessionLog } | { block: Block };

/** run·route·note 는 그대로, open ~ close 는 블록 하나로 묶는다 */
function toItems(logs: SessionLog[]): Item[] {
  const items: Item[] = [];
  let block: Block | null = null;
  for (const l of logs) {
    if (l.kind === 'open') {
      block = { title: l.text, rows: [] };
      items.push({ block });
    } else if (l.kind === 'close' && block) {
      block.footer = l.text;
      block = null;
    } else if (block && (l.kind === 'row' || l.kind === 'plain')) {
      block.rows.push(l);
    } else {
      items.push({ log: l });
    }
  }
  return items;
}

function Rows({ rows }: { rows: SessionLog[] }) {
  let section: string | null = null;
  return (
    <div className="flex flex-col gap-0.5 px-3 pb-2">
      {rows.flatMap((r, i) => {
        const head = r.section && r.section !== section;
        section = r.section ?? section;
        return [
          head && <div key={`s${i}`} className="mt-1.5 font-semibold text-accent">{r.section}</div>,
          r.kind === 'plain' ? (
            <div key={i} className="whitespace-pre">{r.text}</div>
          ) : (
            <div key={i} className="flex gap-3">
              <span className="w-20 shrink-0 text-muted">{r.label}</span>
              <span className="min-w-0 break-words">{r.text}</span>
            </div>
          ),
        ];
      })}
    </div>
  );
}

/** 실행 로그. 에이전트별 블록 → 섹션(받은 입력·동작·근거·결과) → 라벨·값. 터미널·outputs/logs/<세션>.log 와 같은 내용 */
export function AgentLog({ sessionId, running, open, onClose }: { sessionId: string; running: boolean; open: boolean; onClose: () => void }) {
  const { data: logs = [], isError, error } = useSessionLogs(sessionId, open, running);
  const items = useMemo(() => toItems(logs), [logs]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && running) bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [open, running, logs.length]);

  return (
    <Modal title="실행 로그" open={open} onClose={onClose} wide>
      {isError && <p className="text-sm text-warn">{String(error.message)}</p>}
      <p className="mb-3 text-xs text-muted">에이전트마다 받은 입력 → 동작(tool·ReAct·LLM) → 근거 → 결과 순서</p>
      <div className="flex max-h-[70vh] flex-col gap-2 overflow-auto font-mono text-xs leading-relaxed">
        {items.map((item, i) =>
          'block' in item ? (
            <details key={i} open className="shrink-0 rounded-card border border-border">
              <summary className="cursor-pointer px-3 py-2 font-semibold">{item.block.title}</summary>
              <Rows rows={item.block.rows} />
              {item.block.footer && <div className="border-t border-border px-3 py-1.5 text-muted">{item.block.footer}</div>}
            </details>
          ) : item.log.kind === 'run' ? (
            <div key={i} className="mt-2 shrink-0 font-semibold text-accent">{item.log.text}</div>
          ) : item.log.kind === 'route' ? (
            <div key={i} className="shrink-0 pl-3 text-muted">↓ {item.log.text}</div>
          ) : (
            <div key={i} className="flex shrink-0 gap-3 pl-3">
              <span className="w-20 shrink-0 text-muted">{item.log.label}</span>
              <span className="min-w-0 break-words">{item.log.text}</span>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>
    </Modal>
  );
}
