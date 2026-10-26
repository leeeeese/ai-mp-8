import { Outlet } from 'react-router-dom';
import { Aurora } from './Aurora';
import { Sidebar } from './Sidebar';

export function Layout() {
  return (
    <div className="relative min-h-full">
      <Aurora />
      <div className="relative z-10 flex min-h-screen gap-2 p-0 lg:p-2">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-x-clip">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
