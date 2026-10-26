import { lazy, Suspense, type ReactNode } from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Home } from './pages/Home';
import { NotFound } from './pages/NotFound';
import { Session } from './pages/Session';

const Report = lazy(() => import('./pages/Report').then((m) => ({ default: m.Report })));
const Graph = lazy(() => import('./pages/Graph').then((m) => ({ default: m.Graph })));
const Admin = lazy(() => import('./pages/Admin').then((m) => ({ default: m.Admin })));

const page = (el: ReactNode) => <Suspense fallback={<p className="p-6 text-sm text-muted">불러오는 중…</p>}>{el}</Suspense>;

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <Home /> },
      { path: '/session/:id', element: <Session /> },
      { path: '/session/:id/report', element: page(<Report />) },
      { path: '/graph', element: page(<Graph />) },
      { path: '/admin', element: page(<Admin />) },
      { path: '*', element: <NotFound /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
