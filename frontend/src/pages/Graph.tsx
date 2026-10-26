import { AlertTriangle, CheckCircle2, RefreshCw, Search } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useCreateReloadRequest, useGraphStatus, useNeighbors, useNodeSearch } from '@/api/client';
import { SM_LABEL, type SmPart } from '@/api/types';
import { Badge } from '@/components/Badge';
import { GraphPreview } from '@/components/GraphPreview';
import { Modal } from '@/components/Modal';
import { PageHeader } from '@/components/PageHeader';
import { Segmented } from '@/components/Segmented';
import { cn, formatDateTime } from '@/lib/cn';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';

const DEFAULT_NODE: Record<SmPart, string> = { dbm: 'DBIO:DCP_MSGSCRT_S02', prod: 'TP:CombScrbSvc.joinComb' };

function Tile({ label, value, sub, strong }: { label: string; value: string; sub?: string; strong?: boolean }) {
  return (
    <div className={cn('glass p-4', strong && 'shadow-glow')}>
      <div className="text-xs text-muted">{label}</div>
      <div className={cn('mt-1 font-mono text-2xl font-semibold', strong && 'text-accent')}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Graph() {
  const { graphSm: sm, setGraphSm } = useUi();
  const { can } = useAuth();
  const { data: st } = useGraphStatus(sm);
  const [query, setQuery] = useState('');
  const [center, setCenter] = useState<string | null>(DEFAULT_NODE[sm]);
  const [selected, setSelected] = useState<string | null>(null);
  const [reloadOpen, setReloadOpen] = useState(false);
  const [showColumns, setShowColumns] = useState(false);
  const { data: hits = [] } = useNodeSearch(sm, query);
  const { data: hood, isError: hoodError } = useNeighbors(sm, center);
  const location = useLocation();

  useEffect(() => {
    setCenter(DEFAULT_NODE[sm]);
    setSelected(null);
  }, [sm]);
  useEffect(() => {
    if (location.hash === '#pending') document.getElementById('pending')?.scrollIntoView({ behavior: 'smooth' });
  }, [location.hash, st]);
  const onSelect = useCallback((id: string) => setSelected(id), []);
  const node = hood?.nodes.find((n) => n.id === selected);
  const inEdges = hood?.edges.filter((e) => e.to === selected) ?? [];
  const outEdges = hood?.edges.filter((e) => e.from === selected) ?? [];
  const maxLabel = Math.max(1, ...(st?.labels.map((l) => l.count) ?? [1]));
  const issueCount = st?.checks.reduce((n, c) => n + c.items.length, 0) ?? 0;

  return (
    <>
      <PageHeader
        title="적재 현황"
        badges={
          st && (
            <>
              <Badge tone={st.connected ? 'ok' : 'warn'}>{st.connected ? '● Neo4j 연결' : '○ 연결 끊김'}</Badge>
              <span className="hidden text-xs text-muted md:inline">최종 적재 {formatDateTime(st.masterSync.loadedAt)}</span>
            </>
          )
        }
        actions={
          <Segmented
            label="적재 기준 SM 파트"
            options={(['prod', 'dbm'] as SmPart[]).map((p) => ({ value: p, label: SM_LABEL[p] }))}
            value={sm}
            onChange={setGraphSm}
          />
        }
      />
      {!st ? (
        <p className="p-6 text-sm text-muted">불러오는 중…</p>
      ) : (
        <div className="flex flex-col gap-4 p-4 md:p-6">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Tile label="master 반영률" value={`${st.masterSync.percent}%`} sub={`${st.masterSync.loadedCommit} → ${st.masterSync.masterCommit} · ${st.masterSync.behindCommits}커밋 뒤`} strong />
            <Tile label="노드 / 관계" value={`${st.nodes} / ${st.rels}`} sub={`${SM_LABEL[sm]} 영역`} />
            <Tile label="소스 원문 매핑률" value={`${Math.round(st.sourceMapRate * 100)}%`} sub="source_file 로 원문 조회 가능한 노드" />
            <Tile label="점검 필요" value={`${issueCount}건`} sub={`점검 항목 ${st.checks.length}개`} />
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <section className="glass p-4" aria-label="2 hop 미리보기">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">2 hop 미리보기</h2>
                <div className="relative ml-auto w-full sm:w-[320px]">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
                  <input
                    className="input pl-8"
                    placeholder="node_id 또는 이름 검색 (예: TCP_MSG_SCRT)"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    aria-label="노드 검색"
                    role="combobox"
                    aria-expanded={hits.length > 0 && !!query}
                    aria-controls="node-hits"
                  />
                  {query && hits.length > 0 && (
                    <ul id="node-hits" role="listbox" className="glass absolute z-20 mt-1 max-h-64 w-full overflow-y-auto p-1">
                      {hits.map((h) => (
                        <li key={h.id}>
                          <button
                            type="button"
                            role="option"
                            aria-selected={h.id === center}
                            className="flex w-full min-h-[36px] items-center gap-2 rounded-chip px-2 text-left text-xs hover:bg-surface-2"
                            onClick={() => {
                              setCenter(h.id);
                              setSelected(h.id);
                              setQuery('');
                            }}
                          >
                            <span className="node-id truncate">{h.id}</span>
                            <span className="ml-auto shrink-0 text-muted">{h.label}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
              {hoodError && <p className="text-sm text-warn">노드를 찾을 수 없습니다.</p>}
              <label className="mb-2 flex w-fit items-center gap-1.5 text-xs text-muted">
                <input type="checkbox" checked={showColumns} onChange={(e) => setShowColumns(e.target.checked)} /> 컬럼 노드 표시
              </label>
              {hood && <GraphPreview data={hood} showColumns={showColumns} onSelect={onSelect} />}
            </section>

            <aside className="glass p-4" aria-label="노드 상세">
              <h2 className="mb-2 text-sm font-semibold">노드 상세</h2>
              {!node ? (
                <p className="text-sm text-muted">그래프에서 노드를 누르면 원문 위치와 연결을 보여줍니다.</p>
              ) : (
                <dl className="space-y-2 text-sm">
                  <div>
                    <dt className="text-xs text-muted">node_id</dt>
                    <dd className="node-id break-all">{node.id}</dd>
                  </div>
                  <div className="flex gap-4">
                    <div>
                      <dt className="text-xs text-muted">라벨</dt>
                      <dd>{node.label}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-muted">역할</dt>
                      <dd>{node.role}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-muted">hop</dt>
                      <dd className="font-mono">{node.hop}</dd>
                    </div>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">source_file · 라인</dt>
                    <dd className="node-id break-all">
                      {node.sourceFile} : {node.sourceLine}
                    </dd>
                  </div>
                  {(node.desc || node.comment) && (
                    <div>
                      <dt className="text-xs text-muted">설명</dt>
                      <dd>{String(node.desc ?? node.comment)}</dd>
                    </div>
                  )}
                  {node.is_common && <Badge tone="est">공통 노드 (탐색 기본 제외)</Badge>}
                  <div>
                    <dt className="text-xs text-muted">연결 (via)</dt>
                    <dd>
                      <ul className="mt-1 space-y-1 text-xs">
                        {inEdges.map((e) => (
                          <li key={e.from + e.type} className="node-id text-muted">
                            ← {e.type} {e.from}
                            {e.columns && <span className="text-accent"> [{e.columns}]</span>}
                          </li>
                        ))}
                        {outEdges.map((e) => (
                          <li key={e.to + e.type} className="node-id">
                            → {e.type} {e.to}
                            {e.columns && <span className="text-accent"> [{e.columns}]</span>}
                          </li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                </dl>
              )}
            </aside>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <section className="glass p-4" aria-label="노드 라벨별 개수">
              <h2 className="mb-3 text-sm font-semibold">노드 라벨별</h2>
              <ul className="space-y-2">
                {st.labels.map((l) => (
                  <li key={l.label} className="grid grid-cols-[96px_minmax(0,1fr)_48px] items-center gap-2 text-sm">
                    <span className="truncate">
                      {l.label}
                      <span className="ml-1 text-[10px] text-muted">{l.role}</span>
                    </span>
                    <span className="h-2.5 overflow-hidden rounded-full bg-surface-2">
                      <span className="block h-full rounded-full bg-accent" style={{ width: `${(l.count / maxLabel) * 100}%` }} />
                    </span>
                    <span className="text-right font-mono text-xs">{l.count}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="glass p-4" aria-label="품질 점검">
              <h2 className="mb-3 text-sm font-semibold">품질 점검</h2>
              <ul className="space-y-2">
                {st.checks.map((c) => (
                  <li key={c.id}>
                    <details className="group rounded-chip border border-border bg-surface-2 px-3 py-2">
                      <summary className="flex min-h-[28px] cursor-pointer list-none items-center gap-2 text-sm">
                        {c.items.length ? <AlertTriangle size={14} className="text-warn" /> : <CheckCircle2 size={14} className="text-ok" />}
                        {c.title}
                        <span className="ml-auto font-mono text-xs text-muted">{c.items.length}</span>
                      </summary>
                      <p className="mt-1 text-xs text-muted">{c.description}</p>
                      {c.items.length > 0 && (
                        <ul className="mt-1.5 space-y-0.5">
                          {c.items.map((i) => (
                            <li key={i} className="node-id break-all">
                              {i}
                            </li>
                          ))}
                        </ul>
                      )}
                    </details>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <section id="pending" className="glass p-4" aria-label="master 미반영 파일">
            <h2 className="mb-2 text-sm font-semibold">master 미반영 파일 ({st.masterSync.pendingFiles.length})</h2>
            {st.masterSync.pendingFiles.length ? (
              <ul className="space-y-1">
                {st.masterSync.pendingFiles.map((f) => (
                  <li key={f} className="node-id">
                    {f}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">master 최신 커밋까지 모두 반영되었습니다.</p>
            )}
          </section>

          <section className="glass p-4" aria-label="적재 이력">
            <div className="mb-2 flex items-center gap-2">
              <h2 className="text-sm font-semibold">적재 이력</h2>
              <button type="button" className="btn ml-auto" onClick={() => setReloadOpen(true)} disabled={!can('reload.request')}>
                <RefreshCw size={14} /> 재적재 요청
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="table-base min-w-[560px]">
                <thead>
                  <tr>
                    <th scope="col">시각</th>
                    <th scope="col">커밋</th>
                    <th scope="col">노드</th>
                    <th scope="col">관계</th>
                    <th scope="col">실행</th>
                    <th scope="col">결과</th>
                  </tr>
                </thead>
                <tbody>
                  {st.history.map((h) => (
                    <tr key={h.id}>
                      <td className="whitespace-nowrap">{formatDateTime(h.at)}</td>
                      <td className="node-id">{h.commit}</td>
                      <td className="font-mono">{h.nodes}</td>
                      <td className="font-mono">{h.rels}</td>
                      <td>{h.by}</td>
                      <td>
                        <Badge tone={h.result === 'success' ? 'ok' : h.result === 'warning' ? 'est' : 'warn'}>
                          {h.result === 'success' ? '성공' : h.result === 'warning' ? '경고' : '실패'}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
      <ReloadModal sm={sm} open={reloadOpen} onClose={() => setReloadOpen(false)} />
    </>
  );
}

function ReloadModal({ sm, open, onClose }: { sm: SmPart; open: boolean; onClose: () => void }) {
  const create = useCreateReloadRequest();
  const [scope, setScope] = useState('전체');
  const [reason, setReason] = useState('');
  return (
    <Modal title={`재적재 요청 (${SM_LABEL[sm]})`} open={open} onClose={onClose}>
      <form
        className="flex flex-col gap-3"
        onSubmit={async (e) => {
          e.preventDefault();
          await create.mutateAsync({ sm, scope, reason });
          setReason('');
          onClose();
        }}
      >
        <p className="text-xs text-muted">요청만 생성합니다. 관리자 승인 후 적재 파이프라인이 실행합니다 (프론트·Agent 는 그래프에 쓰지 않음).</p>
        <label className="flex flex-col gap-1 text-sm">
          범위
          <input className="input" value={scope} onChange={(e) => setScope(e.target.value)} placeholder="전체 또는 경로 (예: online/java/com/tmk/tos/cp)" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          사유
          <textarea className="input" rows={3} required value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            취소
          </button>
          <button type="submit" className="btn-primary" disabled={!reason.trim() || create.isPending}>
            요청
          </button>
        </div>
      </form>
    </Modal>
  );
}
