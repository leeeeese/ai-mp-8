import { useQueryClient } from '@tanstack/react-query';
import { ArrowUp, Lock, Star } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useSendMessage, useSession, useSessionLogs, useToggleStar } from '@/api/client';
import { openAgentStream } from '@/api/stream';
import { INPUT_KINDS, MESSAGE_KIND_LABEL, SM_LABEL, type AgentNode, type MessageKind, type Session as SessionT } from '@/api/types';
import { AgentLog } from '@/components/AgentLog';
import { AgentProgress, NODES, emptyProgress, reduceProgress, type ProgressState } from '@/components/AgentProgress';
import { AnswerCard } from '@/components/AnswerCard';
import { Badge } from '@/components/Badge';
import { PageHeader } from '@/components/PageHeader';
import { cn } from '@/lib/cn';

/** 끝난 세션은 진행 패널을 완료 상태로 그린다 (단순이면 Simulator·Verifier 생략) */
function finishedProgress(s: SessionT): ProgressState {
  const simple = s.complexity === 'simple';
  const p = emptyProgress();
  for (const n of NODES) p.status[n.node] = simple && (n.node === 'simulator' || n.node === 'verifier') ? 'skipped' : 'done';
  p.counters = { toolCalls: simple ? 5 : 10, exploreLoops: 1, replans: 0 };
  return p;
}

export function Session() {
  const { id = '' } = useParams();
  const qc = useQueryClient();
  const { data: session, isLoading, isError } = useSession(id);
  const send = useSendMessage(id);
  const star = useToggleStar();
  const [progress, setProgress] = useState<ProgressState>(emptyProgress);
  const [kind, setKind] = useState<MessageKind>('chat');
  const [text, setText] = useState('');
  const [logOpen, setLogOpen] = useState(false);
  // 진행 패널의 단계별 상세 로그 펼치기 (항상 접힌 채로 시작). 하나라도 펼쳤을 때만 로그를 불러온다
  const [expanded, setExpanded] = useState<Set<AgentNode>>(() => new Set());
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const running = session?.status === 'running';
  const loaded = !!session;
  const { data: logs = [] } = useSessionLogs(id, expanded.size > 0, !!running);
  useEffect(() => setExpanded(new Set()), [id]); // 다른 세션으로 옮기면 다시 접힘
  const toggleNode = (node: AgentNode) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(node)) next.add(node);
      return next;
    });

  useEffect(() => {
    if (!session) return;
    if (!running) {
      setProgress(finishedProgress(session));
      return;
    }
    setProgress(emptyProgress());
    return openAgentStream(
      id,
      (e) => setProgress((p) => reduceProgress(p, e)),
      () => {
        void qc.invalidateQueries({ queryKey: ['session', id] });
        void qc.invalidateQueries({ queryKey: ['sessions'] });
        void qc.invalidateQueries({ queryKey: ['kpi-records'] }); // 세션마다 KPI 기록 1건 추가 → KPI 페이지 갱신
      },
    );
    // 세션이 바뀌거나, 처음 불러왔거나, 실행 상태가 바뀔 때만 다시 연결
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, loaded, running]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [session?.messages.length]);

  const focusInput = (k: MessageKind) => {
    setKind(k);
    inputRef.current?.focus();
  };
  const submit = () => {
    if (!text.trim()) return;
    send.mutate({ kind, text: text.trim() });
    setText('');
  };
  /** 단순 응답의 "상세 분석 요청": 권장 사유와 참고 신호("참고: " 줄)를 한 줄에 하나씩 reanalysis 후속 입력으로 보낸다.
   *  Simulator·Verifier 가 사유의 노드 ID 를 확인 대상으로 쓰고, "참고:" 줄의 노드는 경로에 없어도 경고만 한다 (줄바꿈 구분 필수) */
  const requestDetail = (reasons: string[], notes: string[]) => {
    const lines = [...reasons, ...notes.map((n) => `참고: ${n}`)];
    send.mutate({ kind: 'reanalysis', text: lines.length ? lines.join('\n') : '상세 분석 요청' });
  };
  const detailRequested = running || send.isPending || !!session?.messages.some((m) => m.kind === 'reanalysis');

  const badges = useMemo(
    () =>
      session && (
        <>
          <Badge tone="accent">SM {SM_LABEL[session.smPart]}</Badge>
          {session.complexity && <Badge>{session.complexity === 'simple' ? '단순' : '복잡'}</Badge>}
          {running && <Badge tone="accent" className="running-glow">분석 중</Badge>}
        </>
      ),
    [session, running],
  );

  if (isLoading) return <PageHeader title="불러오는 중…" />;
  if (isError || !session)
    return (
      <>
        <PageHeader title="세션을 찾을 수 없습니다" />
        <p className="p-6 text-sm text-muted">삭제되었거나 접근 권한이 없는 세션입니다.</p>
      </>
    );

  return (
    <>
      <PageHeader
        title={session.title}
        badges={badges}
        actions={
          <button type="button" className="icon-btn" onClick={() => star.mutate(id)} aria-label={session.starred ? '즐겨찾기 해제' : '즐겨찾기'} aria-pressed={session.starred}>
            <Star size={18} className={cn(session.starred && 'fill-current text-accent')} />
          </button>
        }
      />
      <div className="grid gap-4 p-4 md:p-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-4">
          {session.messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="ml-auto max-w-[85%] whitespace-pre-line rounded-card border border-border bg-[color:var(--accent-soft)] px-4 py-3 text-sm leading-relaxed">
                {m.kind && m.kind !== 'chat' && <div className="mb-1 text-[11px] font-medium text-accent">{MESSAGE_KIND_LABEL[m.kind]}</div>}
                {m.text}
              </div>
            ) : m.answer ? (
              <AnswerCard
                key={m.id}
                answer={m.answer}
                sessionId={id}
                onAnswerInput={() => focusInput('answer')}
                onRequestDetail={requestDetail}
                detailRequested={detailRequested}
              />
            ) : (
              <div key={m.id} className="glass max-w-[85%] px-4 py-3 text-sm">
                {m.text}
              </div>
            ),
          )}
          {running && (
            <div className="glass flex items-center gap-2 px-4 py-3 text-sm text-muted" role="status">
              <span className="h-2 w-2 rounded-full bg-accent running-glow" /> Agent 가 분석하고 있습니다. 우측 진행 패널에서 단계를 확인하세요.
            </div>
          )}
          <div ref={bottomRef} />

          <form
            className="glass sticky bottom-3 mt-auto p-2.5"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="flex flex-wrap items-center gap-1.5 pb-2" role="radiogroup" aria-label="후속 입력 유형">
              {INPUT_KINDS.map((k) => (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={kind === k}
                  onClick={() => setKind(k)}
                  className={cn('chip cursor-pointer', kind === k && 'border-transparent bg-accent text-accent-fg shadow-glow')}
                >
                  {MESSAGE_KIND_LABEL[k]}
                </button>
              ))}
              <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted" title="SM 파트는 세션 시작 시 고정됩니다">
                <Lock size={12} /> SM {SM_LABEL[session.smPart]} 고정
              </span>
            </div>
            <div className="flex items-end gap-2">
              <label htmlFor="followup" className="sr-only">
                {MESSAGE_KIND_LABEL[kind]}
              </label>
              <textarea
                id="followup"
                ref={inputRef}
                rows={2}
                value={text}
                disabled={running}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
                }}
                placeholder={running ? '분석이 끝나면 입력할 수 있습니다' : `${MESSAGE_KIND_LABEL[kind]}…`}
                className="min-h-[44px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted"
              />
              <button type="submit" className="btn-primary h-10 w-10 !px-0" disabled={running || !text.trim()} aria-label="보내기">
                <ArrowUp size={18} />
              </button>
            </div>
          </form>
        </div>

        <aside className="xl:sticky xl:top-[76px] xl:flex xl:max-h-[calc(100vh-92px)] xl:flex-col xl:self-start">
          <AgentProgress state={progress} onOpenLog={() => setLogOpen(true)} logs={logs} expanded={expanded} onToggle={toggleNode} />
        </aside>
      </div>
      <AgentLog sessionId={id} running={!!running} open={logOpen} onClose={() => setLogOpen(false)} />
    </>
  );
}
