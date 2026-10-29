/**
 * Agent 진행 스트림. 실제: GET /api/sessions/:id/stream (SSE, EventSource).
 * mock: 타이머로 같은 이벤트 순서를 흉내 내고, 끝나면 mock 저장소에 답변을 붙인다.
 */
import { USE_MOCK } from '@/config';
import type { AgentEvent, Complexity, SmPart } from './types';

export function openAgentStream(sessionId: string, onEvent: (e: AgentEvent) => void, onDone: () => void): () => void {
  if (!USE_MOCK) {
    const es = new EventSource(`/api/sessions/${sessionId}/stream`);
    es.onmessage = (msg) => {
      const e = JSON.parse(msg.data) as AgentEvent;
      onEvent(e);
      if (e.node === 'composer' && e.status === 'done') {
        es.close();
        onDone();
      }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }

  let cancelled = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  void import('@/mocks/db').then(({ db, plannedComplexity, completeSession }) => {
    const s = db.sessions.find((x) => x.id === sessionId);
    if (!s || cancelled) return;
    const script = mockScript(plannedComplexity(s), s.smPart);
    let t = 0;
    for (const step of script) {
      t += step.after;
      timers.push(setTimeout(() => !cancelled && onEvent(step.event), t));
    }
    timers.push(
      setTimeout(() => {
        if (cancelled) return;
        completeSession(sessionId);
        onDone();
      }, t + 300),
    );
  });
  return () => {
    cancelled = true;
    timers.forEach(clearTimeout);
  };
}

/** 상품(swing) 세션의 근거 예 (L-1 배치 주기). 그 밖은 tos 예 */
const SWING_EVIDENCE = [
  { nodeId: 'COL:TB_BAT_JOB.CRON_EXPR', file: 'source/swing/batch/BAT_JOB_REG.sql', location: 'L1-5' },
  { nodeId: 'MOD:BatchLauncher.runJob', file: 'source/swing/program/BatchLauncher.pgm', location: 'L11-23' },
  { nodeId: 'DBIO:BatJobSql.updateLastRun', file: 'source/swing/sql/BatJobSql.sql', location: 'L36-46' },
];

function mockScript(complexity: Complexity, sm: SmPart): { after: number; event: AgentEvent }[] {
  const c = (toolCalls: number, exploreLoops = 0, replans = 0) => ({ toolCalls, exploreLoops, replans });
  const complex = complexity === 'complex';
  return [
    { after: 200, event: { node: 'supervisor', status: 'running', detail: '요청 접수, SM 파트 고정', counters: c(0) } },
    { after: 300, event: { node: 'planner', status: 'running', detail: '요건 항목 분리 · 변경 유형 판정' } },
    { after: 1300, event: { node: 'planner', status: 'done', elapsedMs: 1300, detail: complex ? '복잡 · 항목 2개 (저장 데이터 추가 / 연계 항목 추가)' : '단순 · 항목 1개 (구조·데이터 위치 질의)' } },
    { after: 200, event: { node: 'explorer', status: 'running', detail: '언급 표현 → 시작 노드 매칭', counters: c(3) } },
    { after: 1100, event: { node: 'explorer', status: 'done', elapsedMs: 1100, detail: complex ? '시작 노드 7 · 경로 82' : '시작 노드 4 · 경로 13', counters: c(complex ? 9 : 4, 1) } },
    { after: 200, event: { node: 'retriever', status: 'running', detail: '경로 노드 원문 조회' } },
    {
      after: 900,
      event: {
        node: 'retriever',
        status: 'done',
        elapsedMs: 900,
        detail: complex ? '근거 74 · 재탐색 요청 없음' : '근거 17',
        counters: c(complex ? 10 : 5, 1),
        evidence: sm === 'prod'
          ? SWING_EVIDENCE
          : complex
          ? [
              { nodeId: 'SQL:tbch1010.make_snd_tgt#2', file: 'source/tos/batch/proc/tbch1010.pc', location: 'L118-154' },
              { nodeId: 'DBIO:DCP_MSGSCRT_S02', file: 'source/tos/batch/dbio/DCP_MSGSCRT_S02.dbio.xml', location: 'L10-52' },
              { nodeId: 'BAT:TBTW1010', file: 'docs/tos/interface/IF-TOS-TWD-021.md' },
              { nodeId: 'COL:TCP_MSG_SCRT.ADVT_YN', file: 'source/tos/db/ddl/TCP_MSG_SCRT.sql', location: 'L11-11' },
            ]
          : [
              { nodeId: 'COL:TCH_MMS_SND_TGT.FAIL_CD', file: 'source/tos/db/ddl/TCH_MMS_SND_TGT.sql', location: 'L32' },
              { nodeId: 'COL:TCH_EXT_SND_TGT.EXT_RSLT_CD', file: 'source/tos/db/ddl/TCH_EXT_SND_TGT.sql', location: 'L20' },
            ],
      },
    },
    ...(complex
      ? [
          { after: 200, event: { node: 'simulator' as const, status: 'running' as const, detail: '요청안 / 대안 비교' } },
          { after: 1400, event: { node: 'simulator' as const, status: 'done' as const, elapsedMs: 1400, detail: '영역 4 · 대안 1' } },
          { after: 200, event: { node: 'verifier' as const, status: 'running' as const, detail: '영역 누락 · 근거 검증' } },
          { after: 800, event: { node: 'verifier' as const, status: 'done' as const, elapsedMs: 800, detail: '통과', counters: c(10, 1, 0) } },
        ]
      : [
          { after: 100, event: { node: 'simulator' as const, status: 'skipped' as const, detail: '단순 요건' } },
          { after: 50, event: { node: 'verifier' as const, status: 'skipped' as const, detail: '단순 요건' } },
        ]),
    { after: 200, event: { node: 'kpi', status: 'running', detail: '실행 로그 집계' } },
    { after: 300, event: { node: 'kpi', status: 'done', elapsedMs: 300 } },
    { after: 200, event: { node: 'composer', status: 'running', detail: complex ? '레포트 · 체크리스트 작성' : '단순 응답 작성' } },
    { after: 900, event: { node: 'composer', status: 'done', elapsedMs: 900 } },
    { after: 50, event: { node: 'supervisor', status: 'done', detail: '완료' } },
  ];
}
