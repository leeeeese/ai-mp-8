import { ArrowLeft, Check, Copy, FileDown, Loader2, Mail, RefreshCw, Send } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { errorText, useAnswerChecklist, useDownloadOutput, useMailDraft, useReport, useSession, useSessions } from '@/api/client';
import {
  DIFFICULTY_LABEL,
  FEASIBILITY_LABEL,
  SM_LABEL,
  STAGE_LABEL,
  type ChecklistItem,
  type ChecklistOwner,
  type OutputKind,
} from '@/api/types';
import { AreaTable } from '@/components/AreaTable';
import { Badge } from '@/components/Badge';
import { Modal } from '@/components/Modal';
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
  const { data: report, isLoading, error: reportError } = useReport(id, version);
  const { data: sessions = [] } = useSessions();
  const { can, smParts } = useAuth();
  const navigate = useNavigate();
  const mail = useMailDraft(id);
  const [mailOpen, setMailOpen] = useState(false);
  const output = useDownloadOutput(id);
  const [notice, setNotice] = useState<string | null>(null);
  // 결과물이 있는 세션(끝난 복잡 분석) 목록. 사이드바와 같은 열람 범위 (전체 열람은 슈퍼 권한)
  const withReport = (can('sessions.all') ? sessions : sessions.filter((s) => smParts.includes(s.smPart))).filter(
    (s) => s.complexity === 'complex' && s.status === 'done',
  );

  if (isLoading || !report)
    return (
      <>
        <PageHeader title="결과물" />
        {reportError && (
          <p className="p-4 text-sm text-muted md:p-6" role="alert">
            결과물을 불러오지 못했습니다: {errorText(reportError)}
          </p>
        )}
      </>
    );
  const current = version ?? report.version;
  const unconfirmed = report.checklist.filter((c) => c.status === 'unconfirmed').length;
  const done = report.checklist.length - unconfirmed;
  const running = session?.status === 'running';
  const busy = (kind: OutputKind) => output.isPending && output.variables?.kind === kind;
  const save = (kind: OutputKind) => {
    setNotice(null);
    output.mutate(
      { kind },
      {
        onSuccess: (got) => setNotice(got === 'md' ? 'PDF 변환을 할 수 없는 환경이라 Markdown(.md)으로 저장했습니다.' : null),
        onError: (e) => setNotice(`저장하지 못했습니다: ${errorText(e)}`),
      },
    );
  };
  const openMail = () => {
    setMailOpen(true);
    mail.mutate();
  };

  return (
    <>
      <PageHeader
        title={session ? `결과물 · ${session.title}` : '결과물'}
        badges={session && <Badge tone="accent">SM {SM_LABEL[session.smPart]}</Badge>}
        actions={
          <>
            {withReport.length > 1 && (
              <label className="flex items-center gap-1.5 text-xs text-muted">
                <span className="hidden whitespace-nowrap sm:inline">세션</span>
                <select
                  className="input max-w-[260px] py-1.5 text-xs"
                  value={id}
                  onChange={(e) => {
                    setVersion(undefined);
                    navigate(`/session/${e.target.value}/report`);
                  }}
                  aria-label="결과물 세션"
                >
                  {withReport.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.title} · {new Date(s.createdAt).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </option>
                  ))}
                </select>
              </label>
            )}
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
          </>
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
            <button type="button" className="btn" disabled={!can('report') || running} onClick={openMail}>
              <Mail size={16} /> 회신 메일 초안
            </button>
            <button type="button" className="btn" disabled={!can('report') || running || busy('report')} onClick={() => save('report')}>
              {busy('report') ? <Loader2 size={16} className="animate-spin" /> : <FileDown size={16} />} PDF 저장
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!can('report') || running || unconfirmed > 0 || busy('handover')}
              onClick={() => save('handover')}
              title={unconfirmed ? `미확정 ${unconfirmed}건이 남아 있습니다` : '확정 답변·수정 대상 노드·영역 판정을 정리한 문서를 저장합니다'}
            >
              {busy('handover') ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />} 개발 전달용 정리본
            </button>
            {unconfirmed > 0 && <span className="self-center text-xs text-muted">미확정 {unconfirmed}건을 확정하면 정리본을 만들 수 있습니다.</span>}
          </div>
          {notice && (
            <p className="text-xs text-muted" role="status">
              {notice}
            </p>
          )}
        </div>

        <aside className="glass flex flex-col gap-4 p-4 2xl:sticky 2xl:top-[76px] 2xl:self-start">
          <div>
            <div className="flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                체크리스트
                {report.stage && <Badge tone={report.stage === 'final_for_dev' ? 'ok' : 'muted'}>{STAGE_LABEL[report.stage]}</Badge>}
              </h2>
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
          {unconfirmed === 0 && report.checklist.length > 0 ? (
            <button type="button" className="btn-primary justify-center" disabled={!can('report') || running || busy('handover')} onClick={() => save('handover')}>
              {busy('handover') ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />} 모두 확정 · 개발 전달용 정리본 받기
            </button>
          ) : null}
          <p className="text-[11px] leading-relaxed text-muted">
            답변을 입력하고 저장하면 그 항목이 확정되고 결과물에 반영됩니다 (몇 초 걸림). 답변 없이 확정하려면 왼쪽 동그라미를 누르세요. 확정한 답변은 회신
            메일 초안(확정 사항)과 개발 전달용 정리본에 들어갑니다.
          </p>
        </aside>
      </div>

      <Modal title="회신 메일 초안" open={mailOpen} onClose={() => setMailOpen(false)} wide>
        <MailPanel mail={mail} />
      </Modal>
    </>
  );
}

function MailPanel({ mail }: { mail: ReturnType<typeof useMailDraft> }) {
  const [copied, setCopied] = useState(false);
  if (mail.isPending)
    return (
      <p className="flex items-center gap-2 text-sm text-muted">
        <Loader2 size={16} className="animate-spin" /> 체크리스트 답변을 반영해 초안을 작성하는 중…
      </p>
    );
  if (mail.isError) return <p className="text-sm text-muted">초안을 만들지 못했습니다: {errorText(mail.error)}</p>;
  if (!mail.data) return null;
  const text = `제목: ${mail.data.subject}\n\n${mail.data.body}`;
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="text-xs text-muted">제목</div>
        <div className="mt-1 text-sm font-medium">{mail.data.subject}</div>
      </div>
      <div>
        <div className="text-xs text-muted">본문</div>
        <pre className="mt-1 max-h-[55vh] overflow-auto whitespace-pre-wrap rounded-chip border border-border bg-surface-2 p-3 font-sans text-sm leading-relaxed">
          {mail.data.body}
        </pre>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn" onClick={() => mail.mutate()}>
          <RefreshCw size={16} /> 다시 작성
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={() =>
            navigator.clipboard.writeText(text).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            })
          }
        >
          {copied ? <Check size={16} /> : <Copy size={16} />} {copied ? '복사됨' : '복사'}
        </button>
      </div>
    </div>
  );
}

function ChecklistRow({ sessionId, item, highlight, editable }: { sessionId: string; item: ChecklistItem; highlight: boolean; editable: boolean }) {
  const save = useAnswerChecklist(sessionId);
  const [answer, setAnswer] = useState(item.answer ?? '');
  const confirmed = item.status === 'confirmed';
  const pending = save.isPending;
  const dirty = answer.trim() !== (item.answer ?? '').trim();
  return (
    <div className={cn('rounded-chip border border-border bg-surface-2 p-3', highlight && item.changed && 'border-[color:var(--accent)]')} aria-busy={pending}>
      <div className="flex items-start gap-2">
        <button
          type="button"
          disabled={!editable || pending}
          onClick={() => save.mutate({ itemId: item.id, answer, status: confirmed ? 'unconfirmed' : 'confirmed' })}
          className={cn('mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border', confirmed ? 'border-transparent bg-accent text-accent-fg' : 'border-border')}
          aria-label={confirmed ? '미확정으로 되돌리기' : '확정으로 표시'}
          aria-pressed={confirmed}
        >
          {pending ? <Loader2 size={14} className="animate-spin" /> : confirmed && <Check size={14} />}
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
        <input
          id={`ans-${item.id}`}
          className="input py-1.5"
          placeholder="답변 입력"
          value={answer}
          disabled={!editable || pending}
          onChange={(e) => setAnswer(e.target.value)}
        />
        <button type="submit" className="btn min-h-[34px] px-3 text-xs" disabled={!editable || pending || !answer.trim() || (confirmed && !dirty)}>
          {pending ? <Loader2 size={14} className="animate-spin" /> : confirmed && !dirty ? '저장됨' : '저장'}
        </button>
      </form>
      {pending && <p className="mt-1.5 text-[11px] text-muted">결과물에 반영하는 중… (Composer)</p>}
      {save.isError && (
        <p className="mt-1.5 text-[11px] text-warn" role="alert">
          저장하지 못했습니다: {errorText(save.error)}
        </p>
      )}
    </div>
  );
}
