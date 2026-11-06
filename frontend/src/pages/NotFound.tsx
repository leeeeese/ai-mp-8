import { Link } from 'react-router-dom';
import { PageHeader } from '@/components/PageHeader';

export function NotFound() {
  return (
    <>
      <PageHeader title="페이지를 찾을 수 없습니다" />
      <div className="p-6 text-sm text-muted">
        <Link to="/" className="text-accent underline">
          홈으로
        </Link>
      </div>
    </>
  );
}
