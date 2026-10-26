import cytoscape, { type Core } from 'cytoscape';
import { useEffect, useRef } from 'react';
import type { Neighborhood } from '@/api/types';
import { useTheme } from '@/store/theme';

const ROLE_COLOR: Record<string, string> = {
  Entry: '#E8743B',
  EntryHandler: '#F2B134',
  Logic: '#7B74D6',
  DataAccess: '#4F8FD1',
  Table: '#3F9D86',
  Column: '#9CCB6B',
};

/** 2 hop 미리보기 (Cytoscape). 노드 색 = 공통 역할, 테두리 = 시작 노드. 컬럼은 기본 숨김, 2 hop 노드 이름은 선택 시 표시 */
export function GraphPreview({ data, showColumns, onSelect }: { data: Neighborhood; showColumns: boolean; onSelect: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const theme = useTheme((s) => s.theme);

  useEffect(() => {
    if (!ref.current) return;
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue('--text').trim();
    const muted = css.getPropertyValue('--muted').trim();
    const accent = css.getPropertyValue('--accent').trim();
    const nodes = data.nodes.filter((n) => showColumns || n.role !== 'Column' || n.hop === 0);
    const ids = new Set(nodes.map((n) => n.id));
    cy.current?.destroy();
    cy.current = cytoscape({
      container: ref.current,
      elements: [
        ...nodes.map((n) => ({ data: { id: n.id, label: n.name, role: n.role, hop: n.hop } })),
        ...data.edges
          .filter((e) => ids.has(e.from) && ids.has(e.to))
          .map((e, i) => ({ data: { id: `e${i}`, source: e.from, target: e.to, type: e.type } })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (ele: cytoscape.NodeSingular) => ROLE_COLOR[ele.data('role')] ?? muted,
            label: (ele: cytoscape.NodeSingular) => (ele.data('hop') < 2 ? ele.data('label') : ''),
            color: text,
            'font-size': 9,
            'font-family': 'JetBrains Mono, monospace',
            'text-valign': 'bottom',
            'text-margin-y': 4,
            width: (ele: cytoscape.NodeSingular) => (ele.data('hop') === 0 ? 26 : ele.data('role') === 'Column' ? 10 : 16),
            height: (ele: cytoscape.NodeSingular) => (ele.data('hop') === 0 ? 26 : ele.data('role') === 'Column' ? 10 : 16),
            'border-width': (ele: cytoscape.NodeSingular) => (ele.data('hop') === 0 ? 3 : 0),
            'border-color': accent,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1,
            'line-color': muted,
            'target-arrow-color': muted,
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.7,
            'curve-style': 'bezier',
            opacity: 0.6,
          },
        },
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': accent, label: 'data(label)' } },
      ],
      layout: { name: 'cose', animate: false, padding: 24, nodeRepulsion: () => 6000 } as cytoscape.LayoutOptions,
      wheelSensitivity: 0.3,
    });
    cy.current.on('tap', 'node', (evt) => onSelect(evt.target.id()));
    return () => {
      cy.current?.destroy();
      cy.current = null;
    };
  }, [data, theme, showColumns, onSelect]);

  return (
    <div className="relative">
      <div ref={ref} className="h-[420px] w-full rounded-chip border border-border bg-surface-2" role="img" aria-label={`${data.center} 기준 2 hop 그래프, 노드 ${data.nodes.length}개 (컬럼 ${showColumns ? '포함' : '제외'})`} />
      <ul className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted" aria-label="범례">
        {Object.entries(ROLE_COLOR).map(([role, color]) => (
          <li key={role} className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} /> {role}
          </li>
        ))}
      </ul>
    </div>
  );
}
