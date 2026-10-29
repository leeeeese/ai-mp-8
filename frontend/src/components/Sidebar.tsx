import { ChevronRight, Database, FileText, Gauge, Home, Shield, Star, X } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { useSessions } from '@/api/client';
import { SM_LABEL, type SessionSummary } from '@/api/types';
import { cn } from '@/lib/cn';
import { MOCK_USERS, useAuth, useAuthStore } from '@/store/auth';
import { useUi } from '@/store/ui';
import { MasterSyncCard } from './MasterSyncCard';
import { OrbitLogo } from './OrbitLogo';

function groupByDay(list: SessionSummary[]) {
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400_000).toDateString();
  const groups: Record<string, SessionSummary[]> = {};
  for (const s of list) {
    const d = new Date(s.createdAt).toDateString();
    const key = d === today ? '오늘' : d === yesterday ? '어제' : '이전';
    (groups[key] ??= []).push(s);
  }
  return groups;
}

const navCls = ({ isActive }: { isActive: boolean }) =>
  cn(
    'flex min-h-[40px] items-center gap-2.5 rounded-chip px-2.5 text-sm transition-colors',
    isActive ? 'bg-[color:var(--accent-soft)] font-medium text-text' : 'text-muted hover:bg-surface-2 hover:text-text',
  );

export function Sidebar() {
  const { user, can, smParts } = useAuth();
  const switchUser = useAuthStore((s) => s.switchUser);
  const { drawerOpen, setDrawer } = useUi();
  const { data: sessions = [] } = useSessions();
  const location = useLocation();
  // SM 파트 무관 전체 열람은 슈퍼 권한. 일반 유저는 자기 SM 파트 세션만 (최종 필터링은 백엔드)
  const visible = can('sessions.all') ? sessions : sessions.filter((s) => smParts.includes(s.smPart));
  const starred = visible.filter((s) => s.starred);
  const latest = visible.find((s) => s.complexity === 'complex' && s.status === 'done');
  const groups = groupByDay(visible);

  return (
    <>
      {drawerOpen && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setDrawer(false)} aria-hidden="true" />}
      <aside
        className={cn(
          'glass fixed inset-y-2 left-2 z-40 flex w-[240px] flex-col gap-3 overflow-y-auto p-3 transition-transform lg:sticky lg:top-2 lg:h-[calc(100vh-16px)] lg:translate-x-0',
          drawerOpen ? 'translate-x-0' : '-translate-x-[110%]',
        )}
        aria-label="주 메뉴"
        onClick={(e) => (e.target as HTMLElement).closest('a') && setDrawer(false)}
      >
        <div className="flex items-center gap-2 px-1">
          <OrbitLogo size="sm" />
          <span className="font-mono text-sm font-semibold">impact-agent</span>
          <button type="button" className="icon-btn ml-auto lg:hidden" onClick={() => setDrawer(false)} aria-label="메뉴 닫기">
            <X size={18} />
          </button>
        </div>

        <nav className="flex flex-col gap-0.5">
          <NavLink to="/" end className={navCls}>
            <Home size={16} /> 홈
          </NavLink>
          <NavLink to="/graph" className={navCls}>
            <Database size={16} /> 적재 현황
          </NavLink>
          <NavLink
            to={latest ? `/session/${latest.id}/report` : '/'}
            className={() => navCls({ isActive: location.pathname.endsWith('/report') })}
          >
            <FileText size={16} /> 결과물
          </NavLink>
          <NavLink to="/kpi" className={navCls}>
            <Gauge size={16} /> KPI 평가
          </NavLink>
          {user.role === 'admin' && (
            <NavLink to="/admin" className={navCls}>
              <Shield size={16} /> 관리
            </NavLink>
          )}
        </nav>

        <section aria-label="세션" className="min-h-0 flex-1">
          {starred.length > 0 && (
            <>
              <h2 className="px-2.5 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted">즐겨찾기</h2>
              {starred.map((s) => (
                <SessionLink key={s.id} s={s} starred />
              ))}
            </>
          )}
          <h2 className="px-2.5 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted">전체 세션</h2>
          {Object.entries(groups).map(([day, list]) => (
            <details key={day} open className="group">
              <summary className="flex min-h-[32px] cursor-pointer list-none items-center gap-1 px-2.5 text-xs text-muted">
                <ChevronRight size={12} className="transition-transform group-open:rotate-90" /> {day}
              </summary>
              <div className="ml-3 border-l border-border pl-1">
                {list.map((s) => (
                  <SessionLink key={s.id} s={s} />
                ))}
              </div>
            </details>
          ))}
        </section>

        <MasterSyncCard />

        <label className="flex items-center gap-2 px-1 text-xs text-muted">
          <span className="whitespace-nowrap">mock 사용자</span>
          <select
            className="input py-1 text-xs"
            value={user.empNo}
            onChange={(e) => switchUser(e.target.value)}
            aria-label="mock 사용자 전환"
          >
            {MOCK_USERS.map((u) => (
              <option key={u.empNo} value={u.empNo}>
                {u.name} ({u.role === 'admin' ? '관리자' : '유저'} · {u.smParts.map((p) => SM_LABEL[p]).join('/')})
              </option>
            ))}
          </select>
        </label>
      </aside>
    </>
  );
}

function SessionLink({ s, starred }: { s: SessionSummary; starred?: boolean }) {
  return (
    <NavLink
      to={`/session/${s.id}`}
      className={({ isActive }) =>
        cn(
          'flex min-h-[34px] items-center gap-2 rounded-chip px-2.5 text-[13px]',
          isActive ? 'bg-surface-2 text-text' : 'text-muted hover:bg-surface-2 hover:text-text',
        )
      }
      title={s.title}
    >
      {starred && <Star size={12} className="shrink-0 fill-current text-accent" />}
      <span className="truncate">{s.title}</span>
      {s.status === 'running' && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-accent running-glow" aria-label="분석 중" />}
    </NavLink>
  );
}
