/** REST 호출 + TanStack Query 훅. mock 모드에서는 MSW 가 같은 경로를 가로챈다. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  AuditLog,
  ChecklistItem,
  CreateSessionRequest,
  GraphStatus,
  KpiRecord,
  MessageKind,
  Neighborhood,
  ReloadRequest,
  Report,
  Session,
  SessionSummary,
  SmPart,
  User,
} from './types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

const json = (body: unknown) => ({ body: JSON.stringify(body) });

// --- 세션 ---------------------------------------------------------------------

export const useSessions = () =>
  useQuery({ queryKey: ['sessions'], queryFn: () => request<SessionSummary[]>('/api/sessions') });

export const useSession = (id: string) =>
  useQuery({ queryKey: ['session', id], queryFn: () => request<Session>(`/api/sessions/${id}`), enabled: !!id });

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSessionRequest) =>
      request<{ sessionId: string }>('/api/sessions', { method: 'POST', ...json(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  });
}

export function useSendMessage(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { kind: MessageKind; text: string }) =>
      request<Session>(`/api/sessions/${id}/messages`, { method: 'POST', ...json(body) }),
    onSuccess: (s) => qc.setQueryData(['session', id], s),
  });
}

export function useToggleStar() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => request<SessionSummary>(`/api/sessions/${id}/star`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  });
}

// --- 결과물 ---------------------------------------------------------------------

export const useReport = (id: string, version?: number) =>
  useQuery({
    queryKey: ['report', id, version ?? 'latest'],
    queryFn: () => request<Report>(`/api/sessions/${id}/report${version ? `?version=${version}` : ''}`),
    enabled: !!id,
  });

export function useAnswerChecklist(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, ...body }: { itemId: string; answer?: string; status: ChecklistItem['status'] }) =>
      request<ChecklistItem>(`/api/sessions/${id}/checklist/${itemId}`, { method: 'PATCH', ...json(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['report', id] }),
  });
}

// --- KPI ------------------------------------------------------------------------

/** 세션별 KPI 기록 (최근 순). 세션이 끝나면 Session 화면이 이 쿼리를 무효화해 KPI 페이지가 갱신된다 */
export const useKpiRecords = () =>
  useQuery({ queryKey: ['kpi-records'], queryFn: () => request<KpiRecord[]>('/api/kpi/records') });

/** KPI 기록 전체 초기화 (같은 시나리오를 반복 테스트할 때). 되돌릴 수 없다 */
export function useResetKpiRecords() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => request<{ deleted: number }>('/api/kpi/records', { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kpi-records'] }),
  });
}

// --- 적재 현황 -------------------------------------------------------------------

export const useGraphStatus = (sm: SmPart) =>
  useQuery({ queryKey: ['graph-status', sm], queryFn: () => request<GraphStatus>(`/api/graph/status?sm=${sm}`) });

export const useNeighbors = (sm: SmPart, nodeId: string | null) =>
  useQuery({
    queryKey: ['neighbors', sm, nodeId],
    queryFn: () => request<Neighborhood>(`/api/graph/neighbors?sm=${sm}&nodeId=${encodeURIComponent(nodeId!)}&hops=2`),
    enabled: !!nodeId,
  });

export const useNodeSearch = (sm: SmPart, q: string) =>
  useQuery({
    queryKey: ['node-search', sm, q],
    queryFn: () => request<{ id: string; name: string; label: string }[]>(`/api/graph/search?sm=${sm}&q=${encodeURIComponent(q)}`),
    enabled: q.trim().length >= 2,
  });

export function useCreateReloadRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { sm: SmPart; scope: string; reason: string }) =>
      request<ReloadRequest>('/api/graph/reload-requests', { method: 'POST', ...json(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reload-requests'] }),
  });
}

// --- 관리 ------------------------------------------------------------------------

export const useUsers = () => useQuery({ queryKey: ['users'], queryFn: () => request<User[]>('/api/admin/users') });

export function useSaveUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ isNew, ...user }: User & { isNew?: boolean }) =>
      request<User>(isNew ? '/api/admin/users' : `/api/admin/users/${user.empNo}`, {
        method: isNew ? 'POST' : 'PATCH',
        ...json(user),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  });
}

export const useReloadRequests = () =>
  useQuery({ queryKey: ['reload-requests'], queryFn: () => request<ReloadRequest[]>('/api/admin/reload-requests') });

export function useDecideReload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approve' | 'reject' }) =>
      request<ReloadRequest>(`/api/admin/reload-requests/${id}/${decision}`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['reload-requests'] });
      qc.invalidateQueries({ queryKey: ['audit-logs'] });
    },
  });
}

export const useAuditLogs = () =>
  useQuery({ queryKey: ['audit-logs'], queryFn: () => request<AuditLog[]>('/api/admin/audit-logs') });
