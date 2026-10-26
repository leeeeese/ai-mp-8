import { ChevronDown, FileDown, FileText, Mail, MessageSquareReply, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { AgentAnswer } from '@/api/types';
import { useAuth } from '@/store/auth';
import { AreaTable } from './AreaTable';
import { Badge, ConfidenceBadge } from './Badge';
import { QueryBlock } from './QueryBlock';

interface Props {
  answer: AgentAnswer;
  sessionId: string;
  onAnswerInput: () => void;
  onSwitchDetail: () => void;
}

/** Agent 답변 카드: Planner 판정 → (접힘) 분석 과정 → 요약 결론 → 판정표/근거 → 확인 필요 → 액션 */
export function AnswerCard({ answer, sessionId, onAnswerInput, onSwitchDetail }: Props) {
  const { can } = useAuth();
  const simple = answer.complexity === 'simple';
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
              <tbody>
                {answer.evidence.map((e) => (
                  <tr key={e.claim}>
                    <td>{e.claim}</td>
                    <td className="text-muted">{e.evidence}</td>
                    <td className="node-id text-muted">{e.location}</td>
                    <td>
                      <ConfidenceBadge value={e.confidence} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
        {simple && answer.changeDetected && (
          <button type="button" className="btn-primary" onClick={onSwitchDetail}>
            <Sparkles size={16} /> 상세 분석으로 전환
          </button>
        )}
      </div>
    </article>
  );
}
