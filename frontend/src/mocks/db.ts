/** mock 백엔드의 메모리 저장소 (새로고침하면 초기화). MSW 핸들러와 진행 스트림 mock 이 함께 쓴다. */
import type {
  AgentAnswer,
  AuditLog,
  ChecklistItem,
  CreateSessionRequest,
  Message,
  ReloadRequest,
  Session,
  User,
} from '@/api/types';
import { H1_ANSWER, SC001_ANSWER, SC006_ANSWER, SC006_DETAIL_REASONS, reportFor } from './fixtures/scenarios';

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
    session('s-h1', '결합상품 A 무선+무선 결합 허용', '결합상품 A(요즘가족결합)를 무선+무선 5회선 이하도 가입 가능하게 하고, 유선이 없으면 상품명을 B 로 노출해 주세요.', H1_ANSWER, { smPart: 'prod', createdAt: iso(60 * 50) }),
  ] as Session[],
  checklists: {} as Record<string, ChecklistItem[]>,
  reloadRequests: [
    { id: 'r1', sm: 'dbm', scope: 'online/java/com/tmk/tos/cp/msg', reason: 'CampMsgService 수정분 반영', requestedBy: '김유저', requestedAt: iso(120), status: 'pending' },
    { id: 'r2', sm: 'prod', scope: '전체', reason: '정기 적재', requestedBy: '이관리', requestedAt: iso(60 * 30), status: 'approved' },
  ] as ReloadRequest[],
  users: [
    { empNo: 'A11001', name: '김유저', role: 'user', smParts: ['dbm'], lastLoginAt: iso(5), active: true },
    { empNo: 'A10001', name: '이관리', role: 'admin', smParts: ['prod', 'dbm'], lastLoginAt: iso(30), active: true },
    { empNo: 'A11855', name: '박상품', role: 'user', smParts: ['prod'], lastLoginAt: iso(60 * 72), active: true },
    { empNo: 'A10544', name: '최퇴사', role: 'user', smParts: ['dbm'], lastLoginAt: iso(60 * 24 * 40), active: false },
  ] as User[],
  audit: [
    { id: 'l1', at: iso(60 * 30), actor: '이관리', action: '재적재 승인', target: 'r2 (상품 전체)' },
    { id: 'l2', at: iso(60 * 48), actor: '이관리', action: '사용자 추가', target: 'A11855 박상품' },
  ] as AuditLog[],
};

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

export function plannedComplexity(s: Session) {
  if (reanalysisRequested.has(s.id)) return 'complex';
  return s.inputType === 'ops_inquiry' ? 'simple' : 'complex';
}

export function completeSession(id: string) {
  const s = db.sessions.find((x) => x.id === id);
  if (!s || s.status === 'done') return;
  const complexAnswer = s.smPart === 'prod' ? H1_ANSWER : SC001_ANSWER;
  const answer = reanalysisRequested.has(id)
    ? { ...complexAnswer, plannerVerdict: '복잡 — 사용자 상세 분석 요청에 따른 재분석' }
    : s.inputType === 'ops_inquiry'
      ? { ...SC006_ANSWER, changeDetected: true, detailReasons: SC006_DETAIL_REASONS }
      : complexAnswer;
  s.complexity = answer.complexity;
  s.status = 'done';
  s.messages.push({ id: nextId('m-'), role: 'agent', answer, createdAt: new Date().toISOString() });
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
