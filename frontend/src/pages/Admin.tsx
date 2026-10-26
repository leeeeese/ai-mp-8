import { Check, Pencil, UserPlus, X } from 'lucide-react';
import { useState } from 'react';
import { useAuditLogs, useDecideReload, useReloadRequests, useSaveUser, useUsers } from '@/api/client';
import { SM_LABEL, type Role, type SmPart, type User } from '@/api/types';
import { Badge } from '@/components/Badge';
import { Modal } from '@/components/Modal';
import { PageHeader } from '@/components/PageHeader';
import { RequirePermission } from '@/components/RequirePermission';
import { Tabs } from '@/components/Tabs';
import { formatDateTime } from '@/lib/cn';
import { BASE_PERMISSIONS, PERMISSION_LABEL, SUPER_PERMISSIONS } from '@/store/auth';

type Tab = 'users' | 'perms' | 'reload' | 'audit';
const ROLE_LABEL: Record<Role, string> = { user: '유저', admin: '관리자' };

export function Admin() {
  const [tab, setTab] = useState<Tab>('users');
  return (
    <RequirePermission permission="users.manage">
      <PageHeader title="관리" badges={<Badge tone="accent">관리자</Badge>} />
      <div className="flex flex-col gap-4 p-4 md:p-6">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { value: 'users', label: '사용자' },
            { value: 'perms', label: '권한 구성' },
            { value: 'reload', label: '재적재 요청' },
            { value: 'audit', label: '감사 로그' },
          ]}
        />
        {tab === 'users' && <Users />}
        {tab === 'perms' && <Permissions />}
        {tab === 'reload' && <ReloadRequests />}
        {tab === 'audit' && <Audit />}
        <p className="text-xs text-muted">화면의 권한 표시는 편의 기능입니다. 최종 권한 검증은 백엔드에서 합니다.</p>
      </div>
    </RequirePermission>
  );
}

function UserForm({ initial, onDone }: { initial?: User; onDone: () => void }) {
  const save = useSaveUser();
  const [u, setU] = useState<User>(initial ?? { empNo: '', name: '', role: 'user', smParts: [], active: true });
  const toggleSm = (p: SmPart) => setU({ ...u, smParts: u.smParts.includes(p) ? u.smParts.filter((x) => x !== p) : [...u.smParts, p] });
  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={async (e) => {
        e.preventDefault();
        await save.mutateAsync({ ...u, isNew: !initial });
        onDone();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        사번
        <input className="input" required pattern="[A-Z][0-9]{5}" title="예: A11001" value={u.empNo} disabled={!!initial} onChange={(e) => setU({ ...u, empNo: e.target.value.toUpperCase() })} />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        이름
        <input className="input" required value={u.name} onChange={(e) => setU({ ...u, name: e.target.value })} />
      </label>
      <fieldset className="flex flex-col gap-1 text-sm">
        <legend className="mb-1">역할</legend>
        <div className="flex gap-3">
          {(['user', 'admin'] as Role[]).map((r) => (
            <label key={r} className="flex min-h-[32px] items-center gap-1.5">
              <input type="radio" name="role" checked={u.role === r} onChange={() => setU({ ...u, role: r })} />
              {ROLE_LABEL[r]}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="flex flex-col gap-1 text-sm">
        <legend className="mb-1">접근 SM 파트</legend>
        <div className="flex gap-3">
          {(['prod', 'dbm'] as SmPart[]).map((p) => (
            <label key={p} className="flex min-h-[32px] items-center gap-1.5">
              <input type="checkbox" checked={u.smParts.includes(p)} onChange={() => toggleSm(p)} />
              {SM_LABEL[p]}
            </label>
          ))}
        </div>
      </fieldset>
      {initial && (
        <label className="flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={u.active} onChange={(e) => setU({ ...u, active: e.target.checked })} /> 활성
        </label>
      )}
      <div className="flex justify-end gap-2 sm:col-span-2">
        {save.isError && <span className="mr-auto self-center text-xs text-warn">{String(save.error.message)}</span>}
        <button type="submit" className="btn-primary" disabled={!u.smParts.length || save.isPending}>
          {initial ? '저장' : '추가'}
        </button>
      </div>
    </form>
  );
}

function Users() {
  const { data: users = [] } = useUsers();
  const [editing, setEditing] = useState<User | null>(null);
  return (
    <>
      <section className="glass p-4">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <UserPlus size={16} /> 사용자 추가
        </h2>
        <UserForm key={users.length} onDone={() => undefined} />
      </section>
      <section className="glass p-4">
        <h2 className="mb-2 text-sm font-semibold">사용자 목록</h2>
        <div className="overflow-x-auto">
          <table className="table-base min-w-[720px]">
            <thead>
              <tr>
                <th scope="col">사번 · 이름</th>
                <th scope="col">역할</th>
                <th scope="col">권한</th>
                <th scope="col">SM 파트</th>
                <th scope="col">최근 접속</th>
                <th scope="col">상태</th>
                <th scope="col">
                  <span className="sr-only">편집</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.empNo}>
                  <td>
                    <span className="node-id">{u.empNo}</span> {u.name}
                  </td>
                  <td>{ROLE_LABEL[u.role]}</td>
                  <td>{u.role === 'admin' ? '기본 + 슈퍼' : '기본'}</td>
                  <td>{u.smParts.map((p) => SM_LABEL[p]).join(', ')}</td>
                  <td className="whitespace-nowrap text-muted">{u.lastLoginAt ? formatDateTime(u.lastLoginAt) : '-'}</td>
                  <td>
                    <Badge tone={u.active ? 'ok' : 'muted'}>{u.active ? '활성' : '비활성'}</Badge>
                  </td>
                  <td>
                    <button type="button" className="icon-btn" onClick={() => setEditing(u)} aria-label={`${u.name} 편집`}>
                      <Pencil size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <Modal title={`사용자 편집 · ${editing?.name ?? ''}`} open={!!editing} onClose={() => setEditing(null)}>
        {editing && <UserForm key={editing.empNo} initial={editing} onDone={() => setEditing(null)} />}
      </Modal>
    </>
  );
}

function Permissions() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {[
        { title: '기본 권한', who: '유저 · 관리자', perms: BASE_PERMISSIONS },
        { title: '슈퍼 권한', who: '관리자', perms: SUPER_PERMISSIONS },
      ].map((g) => (
        <section key={g.title} className="glass p-4">
          <h2 className="text-sm font-semibold">{g.title}</h2>
          <p className="mb-2 text-xs text-muted">{g.who}</p>
          <ul className="space-y-1.5 text-sm">
            {g.perms.map((p) => (
              <li key={p} className="flex items-start gap-2">
                <Check size={14} className="mt-0.5 text-accent" /> {PERMISSION_LABEL[p]}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ReloadRequests() {
  const { data: list = [] } = useReloadRequests();
  const decide = useDecideReload();
  return (
    <section className="glass p-4">
      <h2 className="mb-2 text-sm font-semibold">재적재 요청</h2>
      <div className="overflow-x-auto">
        <table className="table-base min-w-[720px]">
          <thead>
            <tr>
              <th scope="col">요청 시각</th>
              <th scope="col">SM</th>
              <th scope="col">범위</th>
              <th scope="col">사유</th>
              <th scope="col">요청자</th>
              <th scope="col">상태</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.id}>
                <td className="whitespace-nowrap">{formatDateTime(r.requestedAt)}</td>
                <td>{SM_LABEL[r.sm]}</td>
                <td className="node-id">{r.scope}</td>
                <td>{r.reason}</td>
                <td>{r.requestedBy}</td>
                <td>
                  {r.status === 'pending' ? (
                    <div className="flex gap-1">
                      <button type="button" className="btn min-h-[32px] px-3 text-xs" onClick={() => decide.mutate({ id: r.id, decision: 'approve' })}>
                        <Check size={14} /> 승인
                      </button>
                      <button type="button" className="btn min-h-[32px] px-3 text-xs" onClick={() => decide.mutate({ id: r.id, decision: 'reject' })}>
                        <X size={14} /> 반려
                      </button>
                    </div>
                  ) : (
                    <Badge tone={r.status === 'approved' ? 'ok' : 'muted'}>{r.status === 'approved' ? '승인' : '반려'}</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Audit() {
  const { data: logs = [] } = useAuditLogs();
  return (
    <section className="glass p-4">
      <h2 className="mb-2 text-sm font-semibold">감사 로그</h2>
      <div className="overflow-x-auto">
        <table className="table-base min-w-[560px]">
          <thead>
            <tr>
              <th scope="col">시각</th>
              <th scope="col">수행자</th>
              <th scope="col">작업</th>
              <th scope="col">대상</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((l) => (
              <tr key={l.id}>
                <td className="whitespace-nowrap">{formatDateTime(l.at)}</td>
                <td>{l.actor}</td>
                <td>{l.action}</td>
                <td>{l.target}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
