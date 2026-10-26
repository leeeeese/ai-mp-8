import { create } from 'zustand';
import type { SmPart } from '@/api/types';

/** 화면 공통 UI 상태: 모바일 사이드바, 적재 현황 기준 SM */
export const useUi = create<{
  drawerOpen: boolean;
  setDrawer: (open: boolean) => void;
  graphSm: SmPart;
  setGraphSm: (sm: SmPart) => void;
}>((set) => ({
  drawerOpen: false,
  setDrawer: (drawerOpen) => set({ drawerOpen }),
  graphSm: 'dbm',
  setGraphSm: (graphSm) => set({ graphSm }),
}));
