/**
 * mock 사용자 전환. 메뉴·버튼 숨김/비활성은 편의 기능이고,
 * 최종 권한 검증은 백엔드에서 한다 (프론트 판단을 신뢰하지 않음).
 */
import { useQuery } from '@tanstack/react-query';
import { create } from 'zustand';
import type { Role, SmPart } from '@/api/types';

export type Permission =
  | 'analyze' // 요건 분석 실행
  | 'report' // 결과물 조회·체크리스트 답변·회신 메일·PDF
  | 'graph.view' // 적재 현황 조회
  | 'reload.request' // 재적재 요청
  | 'reload.approve' // 재적재 승인·실행 (슈퍼)
  | 'settings' // 루프 한도·프롬프트 설정 (슈퍼)
  | 'users.manage' // 사용자 추가·권한 변경 (슈퍼)
  | 'audit.view' // 감사 로그 (슈퍼)
  | 'sessions.all'; // SM 파트 무관 전체 세션 열람 (슈퍼)

export const BASE_PERMISSIONS: Permission[] = ['analyze', 'report', 'graph.view', 'reload.request'];
export const SUPER_PERMISSIONS: Permission[] = ['reload.approve', 'settings', 'users.manage', 'audit.view', 'sessions.all'];

export const PERMISSION_LABEL: Record<Permission, string> = {
  analyze: '요건 분석 실행',
  report: '결과물 조회·체크리스트 답변·회신 메일 초안·PDF 저장',
  'graph.view': '적재 현황 조회',
  'reload.request': '재적재 요청',
  'reload.approve': '재적재 승인·실행',
  settings: '루프 한도·프롬프트 설정',
  'users.manage': '사용자 추가·권한 변경',
  'audit.view': '감사 로그 조회',
  'sessions.all': 'SM 파트 무관 전체 세션 열람',
};

export interface AuthUser {
  name: string;
  role: Role;
  smParts: SmPart[];
}

export const MOCK_USERS: AuthUser[] = [
  { name: '이세은', role: 'user', smParts: ['prod'] },
  { name: '권예지', role: 'user', smParts: ['dbm'] },
  { name: '김관리', role: 'admin', smParts: ['prod', 'dbm'] },
];

interface AuthState {
  user: AuthUser;
  switchUser: (name: string) => void;
}

const USER_KEY = 'impact-mock-user';

function savedUser(): AuthUser {
  try {
    return MOCK_USERS.find((u) => u.name === localStorage.getItem(USER_KEY)) ?? MOCK_USERS[0];
  } catch {
    return MOCK_USERS[0];
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: savedUser(),
  switchUser: (name) => {
    try {
      localStorage.setItem(USER_KEY, name);
    } catch {
      /* 저장 불가 환경은 무시 */
    }
    set({ user: MOCK_USERS.find((u) => u.name === name) ?? MOCK_USERS[0] });
  },
}));

/** 역할·SM 파트·권한은 백엔드(/api/auth/me) 기준. 응답이 없으면(연결 전) mock 사용자 값으로 그린다 */
export function useAuth() {
  const mock = useAuthStore((s) => s.user);
  const { data: me } = useQuery({
    queryKey: ['me', mock.name],
    queryFn: async () => {
      const res = await fetch('/api/auth/me', { headers: { 'X-User': encodeURIComponent(mock.name) } });
      return res.ok ? ((await res.json()) as AuthUser & { permissions: Permission[] }) : null;
    },
    staleTime: 60_000,
  });
  const user: AuthUser = me ?? mock;
  const perms = me?.permissions ?? (mock.role === 'admin' ? [...BASE_PERMISSIONS, ...SUPER_PERMISSIONS] : BASE_PERMISSIONS);
  return {
    user,
    role: user.role,
    smParts: user.smParts,
    can: (p: Permission) => perms.includes(p),
  };
}
