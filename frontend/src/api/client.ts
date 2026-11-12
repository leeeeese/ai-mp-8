/** REST 호출 + TanStack Query 훅. mock 모드에서는 MSW 가 같은 경로를 가로챈다. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  AuditLog,
  ChecklistItem,
  CreateSessionRequest,
  GraphStatus,
  KpiRecord,
  MailDraft,
  MessageKind,
  Neighborhood,
  OutputFormat,
  OutputKind,
  ReloadRequest,
  Report,
  Session,
  SessionLog,
  SessionSummary,
  SmPart,
  User,
} from './types';
import { useAuthStore } from '@/store/auth';

async function send(url: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-User': encodeURIComponent(useAuthStore.getState().user.name), ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  return (await send(url, init)).json() as Promise<T>;
}

const json = (body: unknown) => ({ body: JSON.stringify(body) });

// --- 세션 ---------------------------------------------------------------------

export const useSessions = () =>
  useQuery({ queryKey: ['sessions'], queryFn: () => request<SessionSummary[]>('/api/sessions') });

export const useSession = (id: string) =>
  useQuery({ queryKey: ['session', id], queryFn: () => request<Session>(`/api/sessions/${id}`), enabled: !!id });

/** 상세 실행 로그. 분석 중에는 2초마다 다시 읽는다 */
export const useSessionLogs = (id: string, enabled: boolean, running: boolean) =>
  useQuery({
    queryKey: ['session-logs', id],
    queryFn: () => request<SessionLog[]>(`/api/sessions/${id}/logs`),
    enabled: enabled && !!id,
    refetchInterval: running ? 2000 : false,
  });

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
    // 사이드바 목록과 지금 보고 있는 세션(머리말 별) 둘 다 새로 읽는다
    onSuccess: (_s, id) => {
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['session', id] });
    },
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

/** 회신 메일 초안. 체크리스트 답변(확정 사항)까지 반영해 매번 새로 만든다 */
export const useMailDraft = (id: string) =>
  useMutation({ mutationFn: () => request<MailDraft>(`/api/sessions/${id}/outputs/mail`, { method: 'POST' }) });

/** 결과물·개발 전달용 정리본 파일을 받아 저장한다. 실제로 받은 형식(PDF 변환이 안 되면 md)을 돌려준다 */
export async function downloadOutput(id: string, kind: OutputKind, format: OutputFormat = 'pdf'): Promise<OutputFormat> {
  const res = await send(`/api/sessions/${id}/outputs/${kind}?format=${format}`);
  const got = (res.headers.get('X-Output-Format') as OutputFormat | null) ?? format;
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get('Content-Disposition') ?? '')?.[1] ?? `${kind}_${id.slice(0, 8)}.${got}`;
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: decodeURIComponent(name) });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return got;
}

export const useDownloadOutput = (id: string) =>
  useMutation({ mutationFn: ({ kind, format }: { kind: OutputKind; format?: OutputFormat }) => downloadOutput(id, kind, format) });

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
      request<User>(isNew ? '/api/admin/users' : `/api/admin/users/${encodeURIComponent(user.name)}`, {
        method: isNew ? 'POST' : 'PATCH',
        ...json(user),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] });
      qc.invalidateQueries({ queryKey: ['me'] });
    },
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
