import { ChevronDown, FileDown, FileText, Mail, MessageSquareReply, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { AgentAnswer, EvidenceRow } from '@/api/types';
import { useAuth } from '@/store/auth';
import { AreaTable } from './AreaTable';
import { Badge, ConfidenceBadge } from './Badge';
import { QueryBlock } from './QueryBlock';

interface Props {
  answer: AgentAnswer;
  sessionId: string;
  onAnswerInput: () => void;
  /** 상세 분석 요청 (reanalysis 후속 입력). 권장 사유와 참고 신호를 그대로 넘긴다 */
  onRequestDetail: (reasons: string[], notes: string[]) => void;
  /** 이미 요청했거나 분석 중이면 버튼을 막는다 */
  detailRequested: boolean;
}

/** 근거 표에서 바로 펼쳐 보여 줄 행 수. 백엔드가 [변경] 노드 근거부터 정렬해 보내므로 앞쪽이 핵심이다 */
const SHOWN_EVIDENCE = 10;

/** Agent 답변 카드: Planner 판정 → (접힘) 분석 과정 → 요약 결론 → 판정표/근거 → 상세 분석 권장·참고 신호 → 확인 필요 → 액션 */
export function AnswerCard({ answer, sessionId, onAnswerInput, onRequestDetail, detailRequested }: Props) {
  const { can } = useAuth();
  const simple = answer.complexity === 'simple';
  const reasons = answer.detail_analysis_reasons ?? [];
  const notes = answer.detail_analysis_notes ?? [];
  const recommended = simple && !!answer.detail_analysis_recommended;
  // 권장이 아니어도 참고 신호가 있으면 사용자가 직접 상세 분석을 요청할 수 있다 (권장은 좁게, 확인 범위는 넓게)
  const canRequestDetail = simple && (recommended || notes.length > 0);
  const evidenceRow = (e: EvidenceRow) => (
    <tr key={`${e.claim}-${e.evidence}-${e.location}`}>
      <td>{e.claim}</td>
      <td className="text-muted">{e.evidence}</td>
      <td className="node-id text-muted">{e.location}</td>
      <td>
        <ConfidenceBadge value={e.confidence} />
      </td>
    </tr>
  );
  return (
    <article className="glass flex flex-col gap-4 p-4 md:p-5" aria-label="Agent 답변">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Sparkles size={16} className="text-accent" />
        <span className="font-mono text-xs text-muted">Planner 판정</span>
        <span>{answer.plannerVerdict}</span>
      </div>

      <details className="group rounded-chip border border-border bg-surface-2 px-3 py-2">
        <summary className="flex min-h-[28px] cursor-pointer list-none items-center gap-1.5 text-xs text-muted">
          <ChevronDown size={14} className="transition-transform group-open:rotate-180" /> 분석 과정 ({answer.process.length}단계)
        </summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-muted">
          {answer.process.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ol>
      </details>

      <section>
        <h3 className="mb-1.5 text-sm font-semibold">요약 결론</h3>
        <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed">
          {answer.summary.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </section>

      {!simple && answer.areas.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-sm font-semibold">영역별 판정</h3>
          <AreaTable areas={answer.areas} />
        </section>
      )}

      {simple && answer.evidence && (
        <section>
          <h3 className="mb-1.5 text-sm font-semibold">근거</h3>
          <div className="overflow-x-auto">
            <table className="table-base min-w-[560px]">
              <thead>
                <tr>
                  <th scope="col">내용</th>
                  <th scope="col">근거</th>
                  <th scope="col">위치</th>
                  <th scope="col">확인</th>
                </tr>
              </thead>
              <tbody>{answer.evidence.slice(0, SHOWN_EVIDENCE).map(evidenceRow)}</tbody>
            </table>
          </div>
          {answer.evidence.length > SHOWN_EVIDENCE && (
            <details className="group mt-2">
              <summary className="flex min-h-[28px] cursor-pointer list-none items-center gap-1.5 text-xs text-muted">
                <ChevronDown size={14} className="transition-transform group-open:rotate-180" /> 근거 {answer.evidence.length - SHOWN_EVIDENCE}건 더 보기
              </summary>
              <div className="overflow-x-auto">
                <table className="table-base min-w-[560px]">
                  <tbody>{answer.evidence.slice(SHOWN_EVIDENCE).map(evidenceRow)}</tbody>
                </table>
              </div>
            </details>
          )}
        </section>
      )}

      {simple && answer.checkQueries && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">확인 쿼리</h3>
          <p className="text-xs text-muted">Agent 는 고객 데이터를 조회하지 않습니다. 복사해서 직접 실행한 뒤 결과를 "조회 결과 입력"으로 알려 주세요.</p>
          {answer.checkQueries.map((q) => (
            <QueryBlock key={q.title} title={q.title} sql={q.sql} />
          ))}
        </section>
      )}

      {recommended && (
        <section className="flex flex-col gap-2 rounded-chip border border-border bg-[color:var(--accent-soft)] px-3 py-2.5" aria-label="상세 분석 권장">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Badge tone="accent">상세 분석 권장</Badge>
            <span className="font-normal text-muted">탐색 결과 영향이 더 넓을 수 있습니다. 요청하면 복잡 요건으로 다시 분석합니다.</span>
          </div>
          {reasons.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed">
              {reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {simple && notes.length > 0 && (
        <section className="flex flex-col gap-2 rounded-chip border border-border bg-surface-2 px-3 py-2.5" aria-label="참고 신호">
          <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            <Badge>참고 신호</Badge>
            <span className="font-normal text-muted">
              상세 분석을 권장할 정도는 아니지만 확인해 볼 만한 탐색 신호입니다. 상세 분석을 요청하면 함께 확인합니다.
            </span>
          </div>
          <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-muted">
            {notes.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
      )}

      {answer.openItemCount > 0 && (
        <div className="flex items-center gap-2 text-sm">
          <Badge tone="warn">확인 필요 {answer.openItemCount}건</Badge>
          <span className="text-muted">업무 확정·개발 확인이 필요한 항목이 있습니다.</span>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {!simple && (
          <>
            <Link to={`/session/${sessionId}/report`} className="btn-primary">
              <FileText size={16} /> 레포트 · 체크리스트
            </Link>
            <button type="button" className="btn" disabled={!can('report')}>
              <Mail size={16} /> 회신 메일 초안
            </button>
            <button type="button" className="btn" disabled={!can('report')}>
              <FileDown size={16} /> PDF 저장
            </button>
          </>
        )}
        <button type="button" className="btn" onClick={onAnswerInput}>
          <MessageSquareReply size={16} /> 현업 답변 입력
        </button>
        {canRequestDetail && (
          <button
            type="button"
            className={recommended ? 'btn-primary' : 'btn'}
            disabled={detailRequested}
            onClick={() => onRequestDetail(reasons, notes)}
          >
            <Sparkles size={16} /> {detailRequested ? '상세 분석 요청됨' : '상세 분석 요청'}
          </button>
        )}
      </div>
    </article>
  );
}
