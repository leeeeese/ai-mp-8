import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { RotateCcw } from 'lucide-react';
import { useKpiRecords, useResetKpiRecords } from '@/api/client';
import { SM_LABEL, type KpiMetricName, type KpiRecord, type SmPart } from '@/api/types';
import { Badge } from '@/components/Badge';
import { Modal } from '@/components/Modal';
import { PageHeader } from '@/components/PageHeader';
import { Segmented } from '@/components/Segmented';
import { KPI_MIN, USE_MOCK } from '@/config';
import { cn, formatMs } from '@/lib/cn';

type SmFilter = SmPart | 'all';

const pct = (x: number | null) => (x === null ? '-' : `${(x * 100).toFixed(1)}%`);
const ratio = (num: number, den: number) => (den ? num / den : null);

/** 지표가 있는 기록만으로 평균 (없으면 null = 해당 없음) */
function mean(records: KpiRecord[], name: KpiMetricName): number | null {
  const vs = records.map((r) => r.metrics[name]).filter((v): v is number => v !== undefined);
  return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null;
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="glass p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 font-mono text-2xl font-semibold">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** 기준값이 있는 비율. 기준선 표시 + 충족/미달을 글자로 (색만으로 상태를 알리지 않음) */
function ThresholdMeter({ label, value, target, better, sub }: {
  label: string; value: number | null; target: number; better: 'higher' | 'lower'; sub: string;
}) {
  const ok = value === null ? null : better === 'higher' ? value >= target : value <= target;
  const targetText = better === 'higher' ? `기준 ≥ ${pct(target)}` : target === 0 ? '권장 0' : `기준 ≤ ${pct(target)}`;
  return (
    <div className="glass flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted">{label}</span>
        {ok === null ? <Badge>해당 없음</Badge> : ok ? <Badge tone="ok">✓ 충족</Badge> : <Badge tone="warn">! 미달</Badge>}
      </div>
      <div className="font-mono text-2xl font-semibold">{pct(value)}</div>
      <div className="relative h-1.5 rounded-full bg-surface-2" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={1}
           aria-valuenow={value ?? undefined} aria-valuetext={`${pct(value)} (${targetText})`}>
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (value ?? 0) * 100)}%` }} />
        {target > 0 && <span className="absolute -top-1 h-3.5 w-0.5 rounded bg-[color:var(--text)]" style={{ left: `${target * 100}%` }} aria-hidden="true" />}
      </div>
      <div className="text-xs text-muted">{targetText} · {sub}</div>
    </div>
  );
}

/** KPI 페이지: 서비스가 잘 동작하는지 개발·운영 담당이 보는 측정 (사용자 세션 화면에는 표시하지 않음). 세션이 끝날 때마다 기록이 쌓인다 */
export function Kpi() {
  const { data: records = [], isLoading } = useKpiRecords();
  const reset = useResetKpiRecords();
  const [confirmReset, setConfirmReset] = useState(false);
  const [sm, setSm] = useState<SmFilter>('all');
  const shown = useMemo(() => records.filter((r) => sm === 'all' || r.sm_part === sm), [records, sm]);
  const evaluated = shown.filter((r) => r.evaluable);
  const simple = shown.filter((r) => r.complexity === 'simple');

  // 노드 탐지: 배치 평가와 같이 TP/FN/FP 를 합산한 뒤 비율 (세션별 비율의 평균이 아님)
  const sum = (k: keyof NonNullable<KpiRecord['counts']>) => evaluated.reduce((a, r) => a + (r.counts?.[k] ?? 0), 0);
  const [tp, fn, fp] = [sum('tp'), sum('fn'), sum('fp')];
  const critTotal = sum('critical_total');

  // 상세 분석 권장: 정답셋 사례 중 단순 처리된 것 (complexity_match 0 = 정답 complex)
  const simpleEval = evaluated.filter((r) => r.complexity === 'simple');
  const missedComplex = simpleEval.filter((r) => r.metrics.complexity_match === 0);
  const simpleOk = simpleEval.filter((r) => r.metrics.complexity_match === 1);
  const recommended = (rs: KpiRecord[]) => rs.filter((r) => r.metrics.detail_recommended === 1).length;

  return (
    <>
      <PageHeader
        title="KPI 평가"
        badges={USE_MOCK ? <Badge tone="warn">목업 예시 데이터 · 실제 평가 결과 아님</Badge> : undefined}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented<SmFilter> label="SM 파트" value={sm} onChange={setSm}
              options={[{ value: 'all', label: '전체' }, { value: 'prod', label: SM_LABEL.prod }, { value: 'dbm', label: SM_LABEL.dbm }]} />
            <button type="button" className="btn" onClick={() => setConfirmReset(true)} disabled={records.length === 0 || reset.isPending}>
              <RotateCcw size={16} /> 기록 초기화
            </button>
          </div>
        }
      />
      <div className="flex flex-col gap-6 p-4 md:p-6">
        <p className="text-sm text-muted">
          서비스가 잘 동작하는지 확인하는 개발·운영용 측정입니다. 세션 분석이 끝날 때마다 기록 1건이 쌓입니다.
          정답셋 사례는 탐지·판정 품질까지, 일반 요청은 실행 성능만 계산합니다.
        </p>

        <section aria-labelledby="kpi-run" className="flex flex-col gap-3">
          <h2 id="kpi-run" className="text-sm font-semibold">실행 · 전체 세션 {shown.length}건</h2>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile label="평균 소요 시간" value={shown.length ? formatMs(mean(shown, 'latency_ms') ?? 0) : '-'} sub="세션당 에이전트 실행 시간 합" />
            <Tile label="평균 도구 호출" value={mean(shown, 'tool_calls')?.toFixed(1) ?? '-'} sub="세션당 tool 호출 수" />
            <Tile label="재계획 발생" value={pct(ratio(shown.filter((r) => (r.metrics.replans ?? 0) > 0).length, shown.length))}
                  sub="Verifier 보완 필요로 다시 계획한 세션" />
            <Tile label="상세 분석 권장 · 재분석" value={`${recommended(simple)} / ${shown.filter((r) => r.reanalyzed).length}`}
                  sub={`단순 응답 ${simple.length}건 중 권장 · 재분석 세션 수`} />
          </div>
        </section>

        <section aria-labelledby="kpi-node" className="flex flex-col gap-3">
          <h2 id="kpi-node" className="text-sm font-semibold">노드 탐지 · 정답셋 사례 {evaluated.length}건</h2>
          {evaluated.length === 0 ? (
            <p className="glass p-4 text-sm text-muted">정답셋 사례로 실행한 세션이 아직 없습니다.</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-3">
              <ThresholdMeter label="노드 탐지율" value={ratio(tp, tp + fn)} target={KPI_MIN.recall} better="higher" sub={`TP ${tp} · FN ${fn} 합산`} />
              <ThresholdMeter label="Critical 미탐률" value={ratio(sum('critical_missed'), critTotal)} target={0} better="lower"
                              sub={`Critical 정답 ${critTotal}개`} />
              <ThresholdMeter label="노드 정밀도" value={ratio(tp, tp + fp)} target={KPI_MIN.precision} better="higher" sub={`TP ${tp} · FP ${fp} 합산`} />
            </div>
          )}
        </section>

        <section aria-labelledby="kpi-judge" className="flex flex-col gap-3">
          <h2 id="kpi-judge" className="text-sm font-semibold">
            판정 품질 · 정답셋 사례 {evaluated.length}건 <span className="font-normal text-muted">(측정만, 기준값 없음)</span>
          </h2>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            <Tile label="복잡도 일치율" value={pct(mean(evaluated, 'complexity_match'))} sub="Planner 판정 vs 정답" />
            <Tile label="난이도 일치율" value={pct(mean(evaluated, 'difficulty_match'))} sub="요건 전체 난이도 (복잡)" />
            <Tile label="영역 영향 유형 일치" value={pct(mean(evaluated, 'area_impact_match'))} sub="4개 영역 평균 (복잡)" />
            <Tile label="[변경]/[참조] 판정 정확도" value={pct(mean(evaluated, 'change_tag_accuracy'))} sub="정답 변경·참조 노드 (복잡)" />
            <Tile label="놓친 복잡 요건 붙잡음" value={missedComplex.length ? `${recommended(missedComplex)} / ${missedComplex.length}` : '-'}
                  sub="정답 complex 인데 단순 처리 → 상세 분석 권장" />
            <Tile label="과한 권장" value={simpleOk.length ? `${recommended(simpleOk)} / ${simpleOk.length}` : '-'}
                  sub="정답 simple 인데 상세 분석 권장" />
          </div>
        </section>

        <section aria-labelledby="kpi-records" className="glass flex flex-col gap-3 p-4">
          <h2 id="kpi-records" className="text-sm font-semibold">세션별 기록 (최근 순)</h2>
          {isLoading ? (
            <p className="text-sm text-muted">불러오는 중…</p>
          ) : shown.length === 0 ? (
            <p className="text-sm text-muted">기록이 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="table-base min-w-[880px]">
                <thead>
                  <tr>
                    <th scope="col">시각</th>
                    <th scope="col">세션</th>
                    <th scope="col">SM · flow</th>
                    <th scope="col">정답셋</th>
                    <th scope="col">탐지율</th>
                    <th scope="col">정밀도</th>
                    <th scope="col">시간 · 도구 · 재계획</th>
                    <th scope="col">판정 불일치</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={`${r.session_id}-${r.created_at}`}>
                      <td className="whitespace-nowrap text-muted">{new Date(r.created_at).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })}</td>
                      <td>
                        <Link to={`/session/${r.session_id}`} className="hover:text-accent">{r.title}</Link>
                        {r.reanalyzed && <Badge tone="accent" className="ml-1.5">재분석</Badge>}
                      </td>
                      <td className="whitespace-nowrap">{SM_LABEL[r.sm_part]} · {r.complexity === 'simple' ? '단순' : '복잡'}</td>
                      <td className={cn('whitespace-nowrap', !r.req_id && 'text-muted')}>{r.req_id ?? '일반 요청'}</td>
                      <td className="font-mono">{pct(r.metrics.node_recall ?? null)}</td>
                      <td className="font-mono">{pct(r.metrics.node_precision ?? null)}</td>
                      <td className="whitespace-nowrap font-mono text-muted">
                        {formatMs(r.metrics.latency_ms ?? 0)} · {r.metrics.tool_calls ?? '-'} · {r.metrics.replans ?? 0}
                      </td>
                      <td>
                        {r.judgement_notes.length + r.missed_nodes.length === 0 ? (
                          <span className="text-muted">-</span>
                        ) : (
                          <details>
                            <summary className="cursor-pointer text-xs">
                              불일치 {r.judgement_notes.length} · 미탐 {r.missed_nodes.length}
                            </summary>
                            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted">
                              {r.judgement_notes.map((n) => <li key={n}>{n}</li>)}
                              {r.missed_nodes.map((n) => <li key={n} className="node-id">미탐 {n}</li>)}
                            </ul>
                          </details>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <Modal title="KPI 기록 초기화" open={confirmReset} onClose={() => setConfirmReset(false)}>
        <p className="text-sm">
          쌓인 KPI 기록 {records.length}건을 모두 지웁니다 (SM 필터와 관계없이 전체). 되돌릴 수 없습니다.
          세션과 분석 결과는 지워지지 않고, 이후 끝나는 세션부터 다시 쌓입니다.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn" onClick={() => setConfirmReset(false)}>취소</button>
          <button
            type="button"
            className="btn-primary"
            disabled={reset.isPending}
            onClick={() => reset.mutate(undefined, { onSuccess: () => setConfirmReset(false) })}
          >
            <RotateCcw size={16} /> 초기화
          </button>
        </div>
      </Modal>
    </>
  );
}
