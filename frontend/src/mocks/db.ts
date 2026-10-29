/** mock 백엔드의 메모리 저장소 (새로고침하면 초기화). MSW 핸들러와 진행 스트림 mock 이 함께 쓴다. */
import type {
  AgentAnswer,
  AuditLog,
  ChecklistItem,
  CreateSessionRequest,
  KpiRecord,
  Message,
  ReloadRequest,
  Session,
  User,
} from '@/api/types';
import {
  H1_ANSWER,
  L1_ANSWER,
  L1_COMPLEX_ANSWER,
  L1_KEYWORD,
  L1_REQUEST,
  SC001_ANSWER,
  SC006_ANSWER,
  SC006_DETAIL_REASONS,
  reportFor,
} from './fixtures/scenarios';

const now = Date.now();
const iso = (minAgo: number) => new Date(now - minAgo * 60_000).toISOString();
let seq = 100;
const nextId = (p: string) => `${p}${++seq}`;

function session(id: string, title: string, text: string, answer: AgentAnswer, partial: Partial<Session>): Session {
  const createdAt = partial.createdAt ?? iso(60);
  return {
    id,
    title,
    smPart: 'dbm',
    inputType: 'requirement',
    source: 'mail',
    complexity: answer.complexity,
    starred: false,
    status: 'done',
    createdAt,
    messages: [
      { id: `${id}-u`, role: 'user', text, createdAt },
      { id: `${id}-a`, role: 'agent', answer, createdAt },
    ],
    ...partial,
  };
}

export const db = {
  sessions: [
    session('s-sc001', '캠페인 비마케팅 여부 T world 전송', '캠페인 등록 화면에서 비마케팅 여부를 입력받을 수 있게 하고, 이 값을 T world에 같이 전송해 주세요.', SC001_ANSWER, { starred: true, createdAt: iso(90) }),
    session('s-sc006', '캠페인 발송 실패 사유 확인', '자사(C260900301) 309명 중 198명 실패, 타사(C260900302) 444명 중 415명 실패입니다. 실패 고객의 사유를 알고 싶습니다.', SC006_ANSWER, { inputType: 'ops_inquiry', createdAt: iso(60 * 26) }),
    session('s-l1', '구비서류 자동처리 배치 주기 단축', L1_REQUEST, L1_ANSWER, { smPart: 'prod', createdAt: iso(30) }),
    session('s-h1', '결합상품 A 무선+무선 결합 허용', '결합상품 A(요즘가족결합)를 무선+무선 5회선 이하도 가입 가능하게 하고, 유선이 없으면 상품명을 B 로 노출해 주세요.', H1_ANSWER, { smPart: 'prod', createdAt: iso(60 * 50) }),
  ] as Session[],
  checklists: {} as Record<string, ChecklistItem[]>,
  reloadRequests: [
    { id: 'r1', sm: 'dbm', scope: 'online/java/com/tmk/tos/cp/msg', reason: 'CampMsgService 수정분 반영', requestedBy: '김유저', requestedAt: iso(120), status: 'pending' },
    { id: 'r2', sm: 'prod', scope: '전체', reason: '정기 적재', requestedBy: '이관리', requestedAt: iso(60 * 30), status: 'approved' },
  ] as ReloadRequest[],
  users: [
    { empNo: 'A11001', name: '김유저', role: 'user', smParts: ['prod', 'dbm'], lastLoginAt: iso(5), active: true },
    { empNo: 'A10001', name: '이관리', role: 'admin', smParts: ['prod', 'dbm'], lastLoginAt: iso(30), active: true },
    { empNo: 'A11855', name: '박상품', role: 'user', smParts: ['prod'], lastLoginAt: iso(60 * 72), active: true },
    { empNo: 'A10544', name: '최퇴사', role: 'user', smParts: ['dbm'], lastLoginAt: iso(60 * 24 * 40), active: false },
  ] as User[],
  audit: [
    { id: 'l1', at: iso(60 * 30), actor: '이관리', action: '재적재 승인', target: 'r2 (상품 전체)' },
    { id: 'l2', at: iso(60 * 48), actor: '이관리', action: '사용자 추가', target: 'A11855 박상품' },
  ] as AuditLog[],
};

/**
 * KPI 기록 (목업 예시 수치 — 실제 평가 결과 아님). 실제: Supervisor 가 세션마다 state.kpi 를 저장하고 GET /api/kpi/records 로 조회.
 * 정답셋 사례(req_id)만 품질·판정 지표가 있고, 일반 요청은 성능 지표만 (evaluable=false).
 */
const perf = (complexity: 'simple' | 'complex', replans = 0, loops = 1) =>
  complexity === 'simple'
    ? { latency_ms: 48_000, tool_calls: 9, explore_loops: loops, replans, total_tokens: 61_000 }
    : { latency_ms: 131_000, tool_calls: 19, explore_loops: loops, replans, total_tokens: 142_000 };

function seedRecord(sessionId: string, r: Partial<KpiRecord> & Pick<KpiRecord, 'req_id' | 'complexity'>): KpiRecord {
  const s = db.sessions.find((x) => x.id === sessionId)!;
  return {
    session_id: sessionId, title: s.title, sm_part: s.smPart, created_at: s.createdAt, agent_version: 'v0.4 (mock)',
    evaluable: r.req_id !== null, metrics: {}, missed_nodes: [], false_positive_nodes: [], reanalyzed: false, judgement_notes: [],
    ...r,
  };
}

export const kpiRecords: KpiRecord[] = [
  seedRecord('s-sc001', {
    req_id: 'SC-001', complexity: 'complex',
    counts: { tp: 4, fn: 0, fp: 22, critical_missed: 0, critical_total: 2 },
    metrics: { node_recall: 1, node_miss_rate: 0, critical_miss_rate: 0, node_precision: 4 / 26, complexity_match: 1,
      difficulty_match: 1, feasibility_match: 1, area_impact_match: 1, change_tag_accuracy: 0.9, wrong_path_adopted: 0,
      complex_minor: 0, verifier_unresolved: 0, ...perf('complex') },
    false_positive_nodes: ['SQL:CmpnMapper.selectCmpn', 'SCR:TOSCP0120M'],
    judgement_notes: ['[변경]/[참조] 판정 불일치: COL:TCH_MMS_SND_TGT.NON_MKTG_YN(정답 참조)'],
  }),
  seedRecord('s-sc006', {
    req_id: 'SC-006', complexity: 'simple',
    counts: { tp: 2, fn: 1, fp: 24, critical_missed: 1, critical_total: 3 },
    metrics: { node_recall: 2 / 3, node_miss_rate: 1 / 3, critical_miss_rate: 1 / 3, node_precision: 2 / 26, complexity_match: 1,
      detail_recommended: 1, ...perf('simple') },
    missed_nodes: ['COL:TCP_CMM_CD.CD_NM'],
    judgement_notes: ['정답 simple 인데 상세 분석 권장 (공통 모듈 CTL:CmmCdController.selectCdList 이 경로에 있어 다른 화면·처리 영향 확인 필요)'],
  }),
  seedRecord('s-l1', {
    req_id: 'L-1', complexity: 'simple',
    counts: { tp: 7, fn: 0, fp: 16, critical_missed: 0, critical_total: 0 },
    metrics: { node_recall: 1, node_miss_rate: 0, node_precision: 7 / 23, complexity_match: 1, detail_recommended: 1, ...perf('simple') },
    judgement_notes: ['정답 simple 인데 상세 분석 권장 (공통 모듈 MOD:BatchLauncher.runJob 이 탐색에서 제외되어 다른 배치·처리 영향 확인 필요)'],
  }),
  seedRecord('s-h1', {
    req_id: 'H-1', complexity: 'complex',
    counts: { tp: 5, fn: 0, fp: 14, critical_missed: 0, critical_total: 4 },
    metrics: { node_recall: 1, node_miss_rate: 0, critical_miss_rate: 0, node_precision: 5 / 19, complexity_match: 1,
      difficulty_match: 1, feasibility_match: 1, area_impact_match: 0.75, change_tag_accuracy: 1, complex_minor: 0,
      verifier_unresolved: 0, ...perf('complex', 1, 2) },
    judgement_notes: ['영역 영향 유형 불일치: db 정답 modify/실행 reference_only'],
  }),
];

/** 세션 분석이 끝날 때 KPI 기록 1건 추가 (최근 순). 미리 만든 정답셋 세션의 재분석은 평가 지표까지, 그 밖은 성능 지표만 */
function addKpiRecord(s: Session, answer: AgentAnswer, reanalyzed: boolean) {
  const caseId = s.id === 's-l1' ? 'L-1' : null;
  const complex = answer.complexity === 'complex';
  const evaluable = caseId !== null && reanalyzed;
  const metrics: KpiRecord['metrics'] = { ...perf(answer.complexity) };
  if (!complex) metrics.detail_recommended = answer.detail_analysis_recommended ? 1 : 0;
  if (complex) Object.assign(metrics, { complex_minor: 0, verifier_unresolved: 0 });
  if (evaluable) {
    Object.assign(metrics, { node_recall: 1, node_miss_rate: 0, node_precision: 7 / 18, complexity_match: 0,
      difficulty_match: 1, feasibility_match: 0, area_impact_match: 0.5, change_tag_accuracy: 1, complex_minor: 1 });
  }
  kpiRecords.unshift({
    session_id: s.id, title: s.title, sm_part: s.smPart, complexity: answer.complexity, created_at: new Date().toISOString(),
    agent_version: 'v0.4 (mock)', req_id: evaluable ? caseId : null, evaluable, metrics,
    counts: evaluable ? { tp: 7, fn: 0, fp: 11, critical_missed: 0, critical_total: 0 } : undefined,
    missed_nodes: [], false_positive_nodes: [], reanalyzed,
    judgement_notes: evaluable
      ? ['복잡도: 정답 simple / 실행 complex (사용자 상세 분석 요청)', '구현 가능 여부: 정답 feasible / 실행 review',
         '영역 영향 유형 불일치: batch 정답 modify/실행 review 영역']
      : [],
  });
}

export function checklistOf(sessionId: string, version: number) {
  const s = db.sessions.find((x) => x.id === sessionId);
  const answer = s?.messages.find((m) => m.answer?.complexity === 'complex')?.answer ?? SC001_ANSWER;
  const report = reportFor(answer, version);
  const saved = db.checklists[sessionId];
  if (saved) report.checklist = report.checklist.map((c) => saved.find((x) => x.id === c.id) ?? c);
  return report;
}

/** 새 세션: 진행 스트림이 끝나면 completeSession 으로 답변을 붙인다. */
export function createSession(req: CreateSessionRequest): Session {
  const s: Session = {
    id: nextId('s-'),
    title: req.text.replace(/\s+/g, ' ').slice(0, 28) + (req.text.length > 28 ? '…' : ''),
    smPart: req.smPart,
    inputType: req.inputType,
    source: req.source,
    starred: false,
    status: 'running',
    createdAt: new Date().toISOString(),
    messages: [{ id: nextId('m-'), role: 'user', text: req.text, createdAt: new Date().toISOString() }],
  };
  db.sessions.unshift(s);
  return s;
}

/** 상세 분석(reanalysis)을 요청한 세션: 다음 실행은 Planner 가 complex 로 판정한 것처럼 돈다 */
const reanalysisRequested = new Set<string>();

/** 상품 단순 요건(L-1): 첫 메시지에 L1_KEYWORD 가 들어간 상품 세션 */
const isL1 = (s: Session) => s.smPart === 'prod' && !!s.messages[0]?.text?.includes(L1_KEYWORD);

export function plannedComplexity(s: Session) {
  if (reanalysisRequested.has(s.id)) return 'complex';
  return s.inputType === 'ops_inquiry' || isL1(s) ? 'simple' : 'complex';
}

export function completeSession(id: string) {
  const s = db.sessions.find((x) => x.id === id);
  if (!s || s.status === 'done') return;
  const complexAnswer = s.smPart === 'prod' ? H1_ANSWER : SC001_ANSWER;
  const answer = reanalysisRequested.has(id)
    ? isL1(s)
      ? L1_COMPLEX_ANSWER
      : { ...complexAnswer, plannerVerdict: '복잡 — 사용자 상세 분석 요청에 따른 재분석' }
    : isL1(s)
      ? L1_ANSWER
      : s.inputType === 'ops_inquiry'
        ? { ...SC006_ANSWER, detail_analysis_recommended: true, detail_analysis_reasons: SC006_DETAIL_REASONS }
        : complexAnswer;
  s.complexity = answer.complexity;
  s.status = 'done';
  s.messages.push({ id: nextId('m-'), role: 'agent', answer, createdAt: new Date().toISOString() });
  addKpiRecord(s, answer, reanalysisRequested.has(id));
}

export function addMessage(id: string, kind: Message['kind'], text: string): Session | undefined {
  const s = db.sessions.find((x) => x.id === id);
  if (!s) return undefined;
  s.messages.push({ id: nextId('m-'), role: 'user', kind, text, createdAt: new Date().toISOString() });
  if (kind === 'reanalysis') {
    // 실제: Supervisor.followup → Planner(complex) → Explorer ⇄ Retriever → Simulator → Verifier → Composer
    reanalysisRequested.add(id);
    s.status = 'running';
    return s;
  }
  s.messages.push({
    id: nextId('m-'),
    role: 'agent',
    text:
      kind === 'answer'
        ? '현업 답변을 반영했습니다. 결과물에서 v2 를 확인하세요.'
        : kind === 'query_result'
          ? '조회 결과를 받았습니다. 2차 답변을 정리합니다 (mock).'
          : kind === 'change'
            ? '변경 분석 요청을 접수했습니다. 상세 분석으로 전환합니다 (mock).'
            : '추가 질문을 접수했습니다 (mock 응답).',
    createdAt: new Date().toISOString(),
  });
  return s;
}

export function audit(actor: string, action: string, target: string) {
  db.audit.unshift({ id: nextId('l'), at: new Date().toISOString(), actor, action, target });
}

export { nextId };
