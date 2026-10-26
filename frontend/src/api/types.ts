/**
 * 프론트 ↔ 백엔드 API 타입 (가안). 백엔드 스키마(schemas/schemas.json)와 맞춰 갱신한다.
 * SmPart: 'prod' = 상품 SM (swing), 'dbm' = DBM(마케팅) SM (tos) — 백엔드 SmPart product / marketing 에 대응
 */

export type SmPart = 'prod' | 'dbm';
export type Role = 'user' | 'admin';
export type InputType = 'requirement' | 'ops_inquiry';
export type InputSource = 'mail' | 'itsd' | 'etc';
export type Complexity = 'simple' | 'complex';
export type MessageKind = 'chat' | 'query_result' | 'answer' | 'change';

export const SM_LABEL: Record<SmPart, string> = { prod: '상품', dbm: 'DBM' };
export const INPUT_TYPE_LABEL: Record<InputType, string> = { requirement: '요건서', ops_inquiry: '운영 문의' };
export const SOURCE_LABEL: Record<InputSource, string> = { mail: '메일', itsd: 'ITSD', etc: '기타' };
export const MESSAGE_KIND_LABEL: Record<MessageKind, string> = {
  chat: '추가 질문',
  query_result: '조회 결과 입력',
  answer: '현업 답변 입력',
  change: '변경 분석 요청',
};

// --- 세션 ---------------------------------------------------------------------

export interface CreateSessionRequest {
  smPart: SmPart;
  inputType: InputType;
  source: InputSource;
  text: string;
  files?: string[];
}

export interface SessionSummary {
  id: string;
  title: string;
  smPart: SmPart;
  complexity?: Complexity;
  createdAt: string;
  starred: boolean;
  status: 'running' | 'done';
}

export type Confidence = 'confirmed' | 'inferred';
export type Area = 'screen' | 'db' | 'interface' | 'batch';
export type Feasibility = 'feasible' | 'hard' | 'review';
export type Difficulty = 'Major' | 'Medium' | 'Minor';

export const AREA_LABEL: Record<Area, string> = { screen: '화면', db: 'DB', interface: '연계', batch: '배치' };
export const FEASIBILITY_LABEL: Record<Feasibility, string> = { feasible: '가능', hard: '어려움', review: '검토 필요' };
export const DIFFICULTY_LABEL: Record<Difficulty, string> = { Major: '상', Medium: '중', Minor: '하' };

export interface AreaVerdict {
  area: Area;
  impact: string;
  path: string[]; // 노드 ID (진입 → DB)
  requested: { feasibility: Feasibility; difficulty: Difficulty } | null; // 요청안
  alternative: { feasibility: Feasibility; difficulty: Difficulty } | null; // 대안
  confidence: Confidence;
  changed?: boolean; // 이전 버전 대비 바뀐 행
}

export interface EvidenceRow {
  claim: string;
  evidence: string;
  location: string;
  confidence: Confidence;
}

export interface AgentAnswer {
  complexity: Complexity;
  plannerVerdict: string;
  process: string[]; // 분석 과정 요약 (접힘)
  summary: string[];
  areas: AreaVerdict[];
  openItemCount: number;
  // 단순 응답
  evidence?: EvidenceRow[];
  checkQueries?: { title: string; sql: string }[];
  changeDetected?: boolean; // 단순 → 상세 분석 전환 제안
}

export interface Message {
  id: string;
  role: 'user' | 'agent';
  kind?: MessageKind;
  text?: string;
  answer?: AgentAnswer;
  createdAt: string;
}

export interface Session extends SessionSummary {
  inputType: InputType;
  source: InputSource;
  messages: Message[];
}

// --- Agent 진행 (SSE) -----------------------------------------------------------

export type AgentNode = 'supervisor' | 'planner' | 'explorer' | 'retriever' | 'simulator' | 'verifier' | 'kpi' | 'composer';
export type NodeStatus = 'waiting' | 'running' | 'done' | 'skipped';

export interface AgentCounters {
  toolCalls: number;
  exploreLoops: number;
  replans: number;
}

export interface AgentEvent {
  node: AgentNode;
  status: Exclude<NodeStatus, 'waiting'>;
  detail?: string;
  elapsedMs?: number;
  counters?: AgentCounters;
  evidence?: { nodeId: string; file: string; location?: string }[];
}

// --- 결과물 ---------------------------------------------------------------------

export type ChecklistOwner = 'business' | 'developer';

export interface ChecklistItem {
  id: string;
  owner: ChecklistOwner;
  question: string;
  status: 'confirmed' | 'unconfirmed';
  answer?: string;
  changed?: boolean;
}

export interface Report {
  version: number;
  versions: { version: number; label: string }[];
  feasibility: Feasibility;
  difficulty: Difficulty;
  scheduleUncertainty: '낮음' | '중간' | '높음';
  areas: AreaVerdict[];
  scheduleFactors: string[];
  limits: string[];
  checklist: ChecklistItem[];
}

// --- 적재 현황 -------------------------------------------------------------------

export interface MasterSync {
  percent: number;
  loadedCommit: string;
  masterCommit: string;
  behindCommits: number;
  pendingFiles: string[];
  loadedAt: string;
}

export interface QualityCheck {
  id: string;
  title: string;
  description: string;
  items: string[];
}

export interface LoadHistory {
  id: string;
  at: string;
  commit: string;
  nodes: number;
  rels: number;
  by: string;
  result: 'success' | 'warning' | 'failed';
}

export interface GraphStatus {
  sm: SmPart;
  connected: boolean;
  masterSync: MasterSync;
  nodes: number;
  rels: number;
  sourceMapRate: number; // 노드 source_file 로 원문을 찾을 수 있는 비율 (벡터 DB 없음)
  labels: { label: string; role: string; count: number }[];
  checks: QualityCheck[];
  history: LoadHistory[];
}

export interface GraphNode {
  id: string;
  label: string;
  role: string;
  name: string;
  sourceFile: string;
  sourceLine: number;
  desc?: string;
  comment?: string;
  is_common?: boolean;
  [key: string]: unknown;
}

export interface GraphEdge {
  from: string;
  to: string;
  type: string;
  columns?: string;
  via?: string;
  event?: string;
}

export interface Neighborhood {
  center: string;
  nodes: (GraphNode & { hop: number })[];
  edges: GraphEdge[];
}

export interface ReloadRequest {
  id: string;
  sm: SmPart;
  scope: string;
  reason: string;
  requestedBy: string;
  requestedAt: string;
  status: 'pending' | 'approved' | 'rejected';
}

// --- 관리 ------------------------------------------------------------------------

export interface User {
  empNo: string;
  name: string;
  role: Role;
  smParts: SmPart[];
  lastLoginAt?: string;
  active: boolean;
}

export interface AuditLog {
  id: string;
  at: string;
  actor: string;
  action: string;
  target: string;
}
