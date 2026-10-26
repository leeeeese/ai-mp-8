import { http, HttpResponse } from 'msw';
import type { CreateSessionRequest, GraphEdge, GraphNode, GraphStatus, MessageKind, SmPart, User } from '@/api/types';
import graphSample from './data/graph-sample.json';
import { addMessage, audit, checklistOf, createSession, db, nextId } from './db';

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

  // --- 관리 (백엔드에서 슈퍼 권한 검증 필요) ---
  http.get('/api/admin/users', () => HttpResponse.json(db.users)),
  http.post('/api/admin/users', async ({ request }) => {
    const u = (await request.json()) as User;
    if (db.users.some((x) => x.empNo === u.empNo)) return new HttpResponse('이미 있는 사번', { status: 409 });
    db.users.push({ ...u, active: true });
    audit('이관리', '사용자 추가', `${u.empNo} ${u.name}`);
    return HttpResponse.json(u);
  }),
  http.patch('/api/admin/users/:empNo', async ({ params, request }) => {
    const body = (await request.json()) as Partial<User>;
    const u = db.users.find((x) => x.empNo === params.empNo);
    if (!u) return new HttpResponse('not found', { status: 404 });
    Object.assign(u, body);
    audit('이관리', '권한 변경', `${u.empNo} ${u.name}`);
    return HttpResponse.json(u);
  }),
  http.get('/api/admin/reload-requests', () => HttpResponse.json(db.reloadRequests)),
  http.post('/api/admin/reload-requests/:id/:decision', ({ params }) => {
    const r = db.reloadRequests.find((x) => x.id === params.id);
    if (!r) return new HttpResponse('not found', { status: 404 });
    r.status = params.decision === 'approve' ? 'approved' : 'rejected';
    audit('이관리', r.status === 'approved' ? '재적재 승인' : '재적재 반려', `${r.id} (${r.scope})`);
    return HttpResponse.json(r);
  }),
  http.get('/api/admin/audit-logs', () => HttpResponse.json(db.audit)),
];
