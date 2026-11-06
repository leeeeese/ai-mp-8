import type { ReactNode } from 'react';
import { type Permission, useAuth } from '@/store/auth';

/** 화면 가드 (편의용). 실제 차단은 백엔드 권한 검증으로 한다 */
export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const { can } = useAuth();
  if (can(permission)) return <>{children}</>;
  return (
    <div className="glass mx-auto mt-24 max-w-md p-8 text-center">
      <h1 className="text-lg font-semibold">권한이 없습니다</h1>
      <p className="mt-2 text-sm text-muted">관리자(슈퍼 권한)만 볼 수 있는 화면입니다.</p>
    </div>
  );
}
