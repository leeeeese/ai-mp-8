import { ArrowLeft, Check, FileDown, Mail, Send } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAnswerChecklist, useReport, useSession } from '@/api/client';
import { DIFFICULTY_LABEL, FEASIBILITY_LABEL, SM_LABEL, type ChecklistItem, type ChecklistOwner } from '@/api/types';
import { AreaTable } from '@/components/AreaTable';
import { Badge } from '@/components/Badge';
import { PageHeader } from '@/components/PageHeader';
import { cn } from '@/lib/cn';
import { useAuth } from '@/store/auth';

const OWNER_LABEL: Record<ChecklistOwner, string> = { business: '업무 확정 필요', developer: '개발 담당자 확인' };

function Kpi({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={cn('glass p-4', strong && 'shadow-glow')}>
      <div className="text-xs text-muted">{label}</div>
      <div className={cn('mt-1 font-mono text-2xl font-semibold', strong && 'text-accent')}>{value}</div>
    </div>
  );
}

export function Report() {
  const { id = '' } = useParams();
  const [version, setVersion] = useState<number | undefined>();
  const { data: session } = useSession(id);
  const { data: report, isLoading } = useReport(id, version);
  const { can } = useAuth();

  if (isLoading || !report) return <PageHeader title="결과물" />;
  const current = version ?? report.version;
  const unconfirmed = report.checklist.filter((c) => c.status === 'unconfirmed').length;
  const done = report.checklist.length - unconfirmed;

  return (
    <>
      <PageHeader
        title={session ? `결과물 · ${session.title}` : '결과물'}
        badges={session && <Badge tone="accent">SM {SM_LABEL[session.smPart]}</Badge>}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <span className="hidden whitespace-nowrap sm:inline">버전</span>
            <select className="input py-1.5 text-xs" value={current} onChange={(e) => setVersion(Number(e.target.value))} aria-label="결과물 버전">
              {report.versions.map((v) => (
                <option key={v.version} value={v.version}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
        }
      />
      <div className="grid gap-4 p-4 md:p-6 2xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex min-w-0 flex-col gap-4">
          <Link to={`/session/${id}`} className="inline-flex w-fit items-center gap-1 text-sm text-muted hover:text-text">
            <ArrowLeft size={14} /> 분석 채팅으로
          </Link>
          <div className="grid gap-3 sm:grid-cols-3">
            <Kpi label="반영 가능성" value={FEASIBILITY_LABEL[report.feasibility]} strong />
            <Kpi label="전체 난이도" value={DIFFICULTY_LABEL[report.difficulty]} />
            <Kpi label="일정 불확실성" value={report.scheduleUncertainty} />
          </div>

          <section className="glass p-4">
            <h2 className="mb-2 text-sm font-semibold">영역별 판정</h2>
            {current > 1 && <p className="mb-2 text-xs text-muted">이전 버전 대비 바뀐 행을 강조합니다.</p>}
            <AreaTable areas={report.areas} highlightChanged={current > 1} />
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <section className="glass p-4">
              <h2 className="mb-2 text-sm font-semibold">일정 영향 요인</h2>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {report.scheduleFactors.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </section>
            <section className="glass p-4">
              <h2 className="mb-2 text-sm font-semibold">분석 한계</h2>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
                {report.limits.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </section>
          </div>

          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn" disabled={!can('report')}>
              <Mail size={16} /> 회신 메일 초안
            </button>
            <button type="button" className="btn" disabled={!can('report')}>
              <FileDown size={16} /> PDF 저장
            </button>
            <button type="button" className="btn-primary" disabled={unconfirmed > 0} title={unconfirmed ? `미확정 ${unconfirmed}건이 남아 있습니다` : undefined}>
              <Send size={16} /> 개발 전달용 정리본
            </button>
            {unconfirmed > 0 && <span className="self-center text-xs text-muted">미확정 {unconfirmed}건을 확정하면 정리본을 만들 수 있습니다.</span>}
          </div>
        </div>

        <aside className="glass flex flex-col gap-4 p-4 2xl:sticky 2xl:top-[76px] 2xl:self-start">
          <div>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">체크리스트</h2>
              <span className="font-mono text-xs text-muted">
                {done}/{report.checklist.length}
              </span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label="체크리스트 확정 진행률" aria-valuenow={done} aria-valuemax={report.checklist.length}>
              <div className="h-full rounded-full bg-accent shadow-glow" style={{ width: `${(done / Math.max(report.checklist.length, 1)) * 100}%` }} />
            </div>
          </div>
          {(['business', 'developer'] as ChecklistOwner[]).map((owner) => (
            <section key={owner} className="flex flex-col gap-2">
              <h3 className="text-xs font-medium text-muted">{OWNER_LABEL[owner]}</h3>
              {report.checklist
                .filter((c) => c.owner === owner)
                .map((c) => (
                  <ChecklistRow key={c.id} sessionId={id} item={c} highlight={current > 1} editable={can('report')} />
                ))}
            </section>
          ))}
        </aside>
      </div>
    </>
  );
}

function ChecklistRow({ sessionId, item, highlight, editable }: { sessionId: string; item: ChecklistItem; highlight: boolean; editable: boolean }) {
  const save = useAnswerChecklist(sessionId);
  const [answer, setAnswer] = useState(item.answer ?? '');
  const confirmed = item.status === 'confirmed';
  return (
    <div className={cn('rounded-chip border border-border bg-surface-2 p-3', highlight && item.changed && 'border-[color:var(--accent)]')}>
      <div className="flex items-start gap-2">
        <button
          type="button"
          disabled={!editable}
          onClick={() => save.mutate({ itemId: item.id, answer, status: confirmed ? 'unconfirmed' : 'confirmed' })}
          className={cn('mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border', confirmed ? 'border-transparent bg-accent text-accent-fg' : 'border-border')}
          aria-label={confirmed ? '미확정으로 되돌리기' : '확정으로 표시'}
          aria-pressed={confirmed}
        >
          {confirmed && <Check size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-snug">{item.question}</p>
          <Badge tone={confirmed ? 'ok' : 'warn'} className="mt-1">
            {confirmed ? '확정' : '미확정'}
          </Badge>
          {highlight && item.changed && <span className="ml-1 text-[10px] text-accent">변경</span>}
        </div>
      </div>
      <form
        className="mt-2 flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ itemId: item.id, answer, status: answer.trim() ? 'confirmed' : item.status });
        }}
      >
        <label className="sr-only" htmlFor={`ans-${item.id}`}>
          답변
        </label>
        <input id={`ans-${item.id}`} className="input py-1.5" placeholder="답변 입력" value={answer} disabled={!editable} onChange={(e) => setAnswer(e.target.value)} />
        <button type="submit" className="btn min-h-[34px] px-3 text-xs" disabled={!editable || !answer.trim()}>
          저장
        </button>
      </form>
    </div>
  );
}
