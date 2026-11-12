import { http, HttpResponse } from 'msw';
import type { CreateSessionRequest, GraphEdge, GraphNode, GraphStatus, MessageKind, SmPart, User } from '@/api/types';
import graphSample from './fixtures/graph-sample.json';
import { addMessage, audit, checklistOf, createSession, db, kpiRecords, nextId } from './db';

interface Sample {
  domain: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  labels: Record<string, number>;
  checks: { isolated: string[]; rwNoColumns: string[]; noSourceFile: string[] };
}
const graph = graphSample as unknown as Record<SmPart, Sample>;
const ROLE: Record<string, string> = {
  Screen: 'Entry', Batch: 'Entry', Controller: 'EntryHandler', TP: 'EntryHandler', Service: 'Logic', Module: 'Logic',
  SQL: 'DataAccess', DBIO: 'DataAccess', Table: 'Table', Column: 'Column',
};
const ORDER = Object.keys(ROLE);

const MASTER: Record<SmPart, GraphStatus['masterSync']> = {
  dbm: {
    percent: 92,
    loadedCommit: 'a1c9f3e',
    masterCommit: '7d02b41',
    behindCommits: 3,
    pendingFiles: ['online/java/com/tmk/tos/cp/msg/service/CampMsgService.java', 'online/webapp/js/cp/TOSCP0210M.js'],
    loadedAt: new Date(Date.now() - 3 * 3600_000).toISOString(),
  },
  prod: {
    percent: 100,
    loadedCommit: 'e40b7aa',
    masterCommit: 'e40b7aa',
    behindCommits: 0,
    pendingFiles: [],
    loadedAt: new Date(Date.now() - 26 * 3600_000).toISOString(),
  },
};

function status(sm: SmPart): GraphStatus {
  const g = graph[sm];
  return {
    sm,
    connected: true,
    masterSync: MASTER[sm],
    nodes: g.nodes.length,
    rels: g.edges.length,
    sourceMapRate: (g.nodes.length - g.checks.noSourceFile.length) / g.nodes.length,
    labels: ORDER.filter((l) => g.labels[l]).map((l) => ({ label: l, role: ROLE[l], count: g.labels[l] })),
    checks: [
      { id: 'isolated', title: '고립 노드', description: '어떤 엣지에도 연결되지 않은 노드', items: g.checks.isolated },
      { id: 'rw-columns', title: 'columns 누락 READS/WRITES', description: 'SQL·DBIO → Table 엣지에 접근 컬럼이 비어 있음 (역방향 컬럼 필터가 동작하지 않음)', items: g.checks.rwNoColumns },
      { id: 'source-file', title: 'source_file 누락', description: '원문 위치가 없어 Retriever 가 근거를 읽을 수 없음', items: g.checks.noSourceFile },
      { id: 'meta', title: '정형 메타 미적재', description: '설명·코멘트가 없어 검색 문구(search_text)가 노드 ID 뿐인 노드', items: g.nodes.filter((n) => !n.desc && !n.comment && n.label !== 'Column').slice(0, 12).map((n) => n.id) },
      { id: 'missing-source', title: '미제공 소스 영역', description: 'Job 정의는 있으나 프로그램 소스가 적재 범위 밖', items: sm === 'dbm' ? ['BAT:TBCH1020 → tbch1020.pc', 'BAT:TBCH4020 → tbch4020.pc'] : [] },
    ].map((c) => ({ ...c, items: c.items ?? [] })),
    history: [
      { id: 'h3', at: MASTER[sm].loadedAt, commit: MASTER[sm].loadedCommit, nodes: g.nodes.length, rels: g.edges.length, by: '적재 파이프라인', result: 'success' },
      { id: 'h2', at: new Date(Date.now() - 4 * 86400_000).toISOString(), commit: '5b21c0d', nodes: g.nodes.length - 12, rels: g.edges.length - 20, by: '적재 파이프라인', result: 'warning' },
      { id: 'h1', at: new Date(Date.now() - 9 * 86400_000).toISOString(), commit: '19aa3f2', nodes: g.nodes.length - 40, rels: g.edges.length - 61, by: '적재 파이프라인', result: 'success' },
    ],
  };
}

function neighbors(sm: SmPart, center: string, hops: number) {
  const g = graph[sm];
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const hop = new Map<string, number>([[center, 0]]);
  let frontier = [center];
  for (let h = 1; h <= hops; h++) {
    const next: string[] = [];
    for (const e of g.edges) {
      for (const [a, b] of [[e.from, e.to], [e.to, e.from]]) {
        if (frontier.includes(a) && !hop.has(b)) {
          hop.set(b, h);
          next.push(b);
        }
      }
    }
    frontier = next;
  }
  const ids = new Set(hop.keys());
  return {
    center,
    nodes: [...ids].filter((id) => byId.has(id)).map((id) => ({ ...byId.get(id)!, hop: hop.get(id)! })),
    edges: g.edges.filter((e) => ids.has(e.from) && ids.has(e.to)),
  };
}

const delay = () => new Promise((r) => setTimeout(r, 250));

export const handlers = [
  // --- 세션 ---
  http.get('/api/sessions', async () => {
    await delay();
    return HttpResponse.json(db.sessions.map(({ messages: _m, ...s }) => s));
  }),
  http.post('/api/sessions', async ({ request }) => {
    const s = createSession((await request.json()) as CreateSessionRequest);
    return HttpResponse.json({ sessionId: s.id });
  }),
  http.get('/api/sessions/:id', async ({ params }) => {
    await delay();
    const s = db.sessions.find((x) => x.id === params.id);
    return s ? HttpResponse.json(s) : new HttpResponse('not found', { status: 404 });
  }),
  http.post('/api/sessions/:id/messages', async ({ params, request }) => {
    const body = (await request.json()) as { kind: MessageKind; text: string };
    const s = addMessage(String(params.id), body.kind, body.text);
    return s ? HttpResponse.json(s) : new HttpResponse('not found', { status: 404 });
  }),
  http.post('/api/sessions/:id/star', ({ params }) => {
    const s = db.sessions.find((x) => x.id === params.id);
    if (!s) return new HttpResponse('not found', { status: 404 });
    s.starred = !s.starred;
    return HttpResponse.json(s);
  }),
  http.get('/api/sessions/:id/logs', () => {
    // 에이전트별 블록 (진행 패널의 단계별 펼치기가 agent 로 거른다)
    const row = (agent: string, kind: string, text: string, section: string | null = null, label = '') =>
      ({ at: '10:00:00', agent, kind, section, label, text });
    return HttpResponse.json([
      row('supervisor', 'run', '1차 실행 · 목업 로그 (실제 API 모드에서 상세 로그 표시)'),
      row('supervisor', 'route', 'PLANNER  새 요건'),
      row('planner', 'open', '1 Planner · 10:00:00'),
      row('planner', 'row', '목업 요청 문장', '받은 입력 ← 요청 접수', '요청 문장'),
      row('planner', 'row', 'gpt-6.1-sol PlannerDraft 11.2s · 입력 6,781 · 출력 828 토큰', '동작 · LLM 판정', 'LLM'),
      row('planner', 'row', '단순 — 목업 판정', '결과 → Explorer', '복잡도'),
      row('planner', 'close', '완료 11.2s │ 단순 · 항목 1개 │ 토큰 7,609'),
      row('supervisor', 'route', 'EXPLORER  탐색 시작'),
      row('explorer', 'open', '2-1 Explorer · 10:00:12'),
      row('explorer', 'row', '항목 1 · 목표 EntryHandler · forward · hop 5', '받은 입력 ← Planner', '탐색 명세'),
      row('explorer', 'row', "search_graph_nodes ['안내 문구'] → SCR:MOCK0100 외 2", '동작 · 규칙 탐색', 'tool'),
      row('explorer', 'row', '경로 3 · 노드 8 · 목표 도달 1', '결과 → Retriever', '경로'),
      row('explorer', 'close', '완료 4.1s │ tool 3'),
      row('retriever', 'open', '2-2 Retriever · 10:00:16'),
      row('retriever', 'row', 'read_source SCR:MOCK0100 → screen/MOCK0100.jsp L12-30', '동작 · 원문 읽기', 'tool'),
      row('retriever', 'row', '근거 4 · 근거 없음 0', '결과 → Composer', '근거'),
      row('retriever', 'close', '완료 3.2s │ tool 2'),
      row('supervisor', 'run', '2차 실행 · 상세 분석 요청 (복잡 흐름 목업)'),
      row('simulator', 'open', '2-3 Simulator · 10:01:02'),
      row('simulator', 'row', 'Explorer 경로 4 → 영향 경로 3개', '동작 · 경로 골격 (규칙)', '골격'),
      row('simulator', 'row', 'TBL:MOCK_CMM_CD (진입점 2개)', '동작 · 경로 골격 (규칙)', '공유 노드'),
      row('simulator', 'row', '노드 판정 5건 ([변경] 2) · 영역 4 · 제외 제안 1', '동작 · LLM 판정', '응답'),
      row('simulator', 'row', 'SCR:MOCK0100 — 안내 문구 하드코딩을 공통코드 조회로 변경 (jsp L12)', '동작 · 노드별 [변경]/[참조] 판정 (LLM)', '[변경]'),
      row('simulator', 'row', 'SQL:MockMapper.selectCd — 기존 조회 재사용, 변경 없음', '동작 · 노드별 [변경]/[참조] 판정 (LLM)', '[참조]'),
      row('simulator', 'row', 'P2-1 제외 제안 거절 — 가드: 목표 역할에 도달했고 마지막 노드에 근거가 있음 (LLM 사유: 배치는 무관해 보임)', '동작 · 경로 제외 가드', '거절'),
      row('simulator', 'row', '가능 여부 hard → review: 불가 사유·대안이 없음', '동작 · 규칙 검증·보정 (LLM 판정 → 최종)', 'db'),
      row('simulator', 'row', 'LLM modify · hard · Minor → 최종 modify · review · Minor (보정) | 근거 hop 4/5', '동작 · 규칙 검증·보정 (LLM 판정 → 최종)', 'db'),
      row('simulator', 'close', '완료 9.8s │ screen modify · db modify │ 토큰 12,402'),
      row('verifier', 'open', '2-4 Verifier · 10:01:12'),
      row('verifier', 'row', '이상 없음', '동작 · 규칙 점검', 'area_path_missing'),
      row('verifier', 'row', '경고: P1-2 가 P1-1 에 포함됨', '동작 · 규칙 점검', 'redundant_path'),
      row('verifier', 'row', 'db 대안 1: 근거 있음 — TBL:MOCK_CMM_CD 공통코드 테이블이 경로·근거에 있음', '동작 · 대안 근거 판단 (LLM)', '판단'),
      row('verifier', 'row', '보완 필요 0건 · 경고 1건 · 반복 0건 → 통과 → Composer', '동작 · 재계획 판단', '결정'),
      row('verifier', 'close', '완료 2.4s │ 통과'),
      row('composer', 'open', '3 Composer · 10:00:20'),
      row('composer', 'row', '[변경] SCR:MOCK0100 안내 문구 텍스트 변경', '결과', '결론'),
      row('composer', 'close', '완료 5.0s │ 단순 응답'),
    ]);
  }),
  http.get('/api/sessions/:id/report', async ({ params, request }) => {
    await delay();
    const v = Number(new URL(request.url).searchParams.get('version') ?? 2);
    return HttpResponse.json(checklistOf(String(params.id), v));
  }),
  http.patch('/api/sessions/:id/checklist/:itemId', async ({ params, request }) => {
    const body = (await request.json()) as { answer?: string; status: 'confirmed' | 'unconfirmed' };
    const id = String(params.id);
    const current = checklistOf(id, 2).checklist;
    const item = { ...current.find((c) => c.id === params.itemId)!, ...body };
    db.checklists[id] = current.map((c) => (c.id === item.id ? item : c));
    return HttpResponse.json(item);
  }),

  // --- 분석 후 산출물 (회신 메일 초안 · 결과물 저장 · 개발 전달용 정리본) ---
  http.post('/api/sessions/:id/outputs/mail', async ({ params }) => {
    await delay();
    const report = checklistOf(String(params.id), 2);
    const answered = report.checklist.filter((c) => c.status === 'confirmed' && c.answer);
    const open = report.checklist.filter((c) => c.owner === 'business' && c.status !== 'confirmed');
    const body = [
      '안녕하세요.',
      '',
      '요청하신 건의 영향도 분석 결과를 공유드립니다.',
      '',
      '[영향 영역]',
      ...report.areas.map((a) => `- ${a.area}: ${a.requested?.feasibility ?? '-'} / ${a.requested?.difficulty ?? '-'}`),
      ...(answered.length ? ['', '[확정 사항]', ...answered.map((c) => `- ${c.question} → ${c.answer}`)] : []),
      ...(open.length ? ['', '[확인 요청]', ...open.map((c) => `- ${c.question}`)] : []),
      '',
      '감사합니다.',
    ].join('\n');
    return HttpResponse.json({ to: [], subject: '[영향도 분석] (목업) 요청 건', body });
  }),
  http.get('/api/sessions/:id/outputs/:kind', async ({ params }) => {
    await delay();
    const id = String(params.id);
    const report = checklistOf(id, 2);
    const kind = String(params.kind);
    const open = report.checklist.filter((c) => c.status !== 'confirmed');
    if (kind === 'handover' && open.length) return new HttpResponse(`미확정 항목이 남아 있습니다: ${open.map((c) => c.id).join(', ')}`, { status: 409 });
    const md = [
      kind === 'handover' ? '# 개발 전달용 정리본 (목업)' : `# 영향도 분석 보고서 (${report.stage})`,
      '',
      '| ID | 담당 | 질문 | 답변 | 상태 |',
      '|---|---|---|---|---|',
      ...report.checklist.map((c) => `| ${c.id} | ${c.owner} | ${c.question} | ${c.answer ?? '-'} | ${c.status} |`),
    ].join('\n');
    // 목업은 PDF 변환 없이 MD 로 내려준다 (실서버도 변환 패키지가 없으면 MD)
    return new HttpResponse(md, {
      headers: {
        'Content-Type': 'text/markdown; charset=utf-8',
        'Content-Disposition': `attachment; filename="${kind}_${id.slice(0, 8)}.md"`,
        'X-Output-Format': 'md',
      },
    });
  }),

  // --- KPI (세션별 기록, 최근 순) ---
  http.get('/api/kpi/records', async () => {
    await delay();
    return HttpResponse.json(kpiRecords);
  }),
  http.delete('/api/kpi/records', () => {
    const deleted = kpiRecords.length;
    kpiRecords.length = 0; // 같은 배열을 비워 세션 종료 시 추가(addKpiRecord)가 계속 이 배열에 쌓이게 한다
    return HttpResponse.json({ deleted });
  }),

  // --- 적재 현황 ---
  http.get('/api/graph/status', async ({ request }) => {
    await delay();
    const sm = (new URL(request.url).searchParams.get('sm') ?? 'dbm') as SmPart;
    return HttpResponse.json(status(sm));
  }),
  http.get('/api/graph/search', ({ request }) => {
    const url = new URL(request.url);
    const sm = (url.searchParams.get('sm') ?? 'dbm') as SmPart;
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const hits = graph[sm].nodes
      .filter((n) => n.id.toLowerCase().includes(q) || n.name.toLowerCase().includes(q) || String(n.desc ?? '').includes(q))
      .slice(0, 12)
      .map(({ id, name, label }) => ({ id, name, label }));
    return HttpResponse.json(hits);
  }),
  http.get('/api/graph/neighbors', async ({ request }) => {
    await delay();
    const url = new URL(request.url);
    const sm = (url.searchParams.get('sm') ?? 'dbm') as SmPart;
    const nodeId = url.searchParams.get('nodeId') ?? '';
    const hops = Number(url.searchParams.get('hops') ?? 2);
    if (!graph[sm].nodes.some((n) => n.id === nodeId)) return new HttpResponse('node not found', { status: 404 });
    return HttpResponse.json(neighbors(sm, nodeId, hops));
  }),
  http.post('/api/graph/reload-requests', async ({ request }) => {
    const body = (await request.json()) as { sm: SmPart; scope: string; reason: string };
    const r = { id: nextId('r'), ...body, requestedBy: '현재 사용자', requestedAt: new Date().toISOString(), status: 'pending' as const };
    db.reloadRequests.unshift(r);
    return HttpResponse.json(r);
  }),

  // --- 사용자 (목업은 X-User 헤더를 사용자 목록에서 찾는다) ---
  http.get('/api/auth/me', ({ request }) => {
    const u = db.users.find((x) => x.name === decodeURIComponent(request.headers.get('X-User') ?? ''));
    if (!u) return new HttpResponse('사용자를 선택해 주세요', { status: 401 });
    const base = ['analyze', 'report', 'graph.view', 'reload.request'];
    const extra = ['reload.approve', 'settings', 'users.manage', 'audit.view', 'sessions.all'];
    return HttpResponse.json({ ...u, permissions: u.role === 'admin' ? [...base, ...extra] : base });
  }),

  // --- 관리 (백엔드에서 슈퍼 권한 검증) ---
  http.get('/api/admin/users', () => HttpResponse.json(db.users)),
  http.post('/api/admin/users', async ({ request }) => {
    const u = (await request.json()) as User;
    if (db.users.some((x) => x.name === u.name)) return new HttpResponse('이미 있는 사용자', { status: 409 });
    db.users.push(u);
    audit('김관리', '사용자 추가', u.name);
    return HttpResponse.json(u);
  }),
  http.patch('/api/admin/users/:name', async ({ params, request }) => {
    const body = (await request.json()) as Partial<User>;
    const u = db.users.find((x) => x.name === params.name);
    if (!u) return new HttpResponse('not found', { status: 404 });
    Object.assign(u, body);
    audit('김관리', '권한 변경', u.name);
    return HttpResponse.json(u);
  }),
  http.get('/api/admin/reload-requests', () => HttpResponse.json(db.reloadRequests)),
  http.post('/api/admin/reload-requests/:id/:decision', ({ params }) => {
    const r = db.reloadRequests.find((x) => x.id === params.id);
    if (!r) return new HttpResponse('not found', { status: 404 });
    r.status = params.decision === 'approve' ? 'approved' : 'rejected';
    audit('김관리', r.status === 'approved' ? '재적재 승인' : '재적재 반려', `${r.id} (${r.scope})`);
    return HttpResponse.json(r);
  }),
  http.get('/api/admin/audit-logs', () => HttpResponse.json(db.audit)),
];
