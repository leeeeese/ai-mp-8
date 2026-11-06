import { AREA_LABEL, DIFFICULTY_LABEL, FEASIBILITY_LABEL, type AreaVerdict } from '@/api/types';
import { cn } from '@/lib/cn';
import { ConfidenceBadge } from './Badge';

function Verdict({ v }: { v: AreaVerdict['requested'] }) {
  if (!v) return <span className="text-muted">-</span>;
  return (
    <span className="whitespace-nowrap">
      {FEASIBILITY_LABEL[v.feasibility]} · <span className="font-mono">{DIFFICULTY_LABEL[v.difficulty]}</span>
    </span>
  );
}

/** 영역별 판정표: 영역 / 영향 경로 / 요청안·대안 난이도 / 근거 확인·추정 */
export function AreaTable({ areas, highlightChanged }: { areas: AreaVerdict[]; highlightChanged?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="table-base min-w-[640px]">
        <thead>
          <tr>
            <th scope="col">영역</th>
            <th scope="col">영향 · 경로</th>
            <th scope="col">요청안</th>
            <th scope="col">대안</th>
            <th scope="col">근거</th>
          </tr>
        </thead>
        <tbody>
          {areas.map((a) => (
            <tr key={a.area} className={cn(highlightChanged && a.changed && 'bg-[color:var(--accent-soft)]')}>
              <td className="whitespace-nowrap font-medium">
                {AREA_LABEL[a.area]}
                {highlightChanged && a.changed && <span className="ml-1 text-[10px] text-accent">변경</span>}
              </td>
              <td>
                <div>{a.impact}</div>
                <div className="node-id mt-1 text-muted">{a.path.join(' → ')}</div>
              </td>
              <td>
                <Verdict v={a.requested} />
              </td>
              <td>
                <Verdict v={a.alternative} />
              </td>
              <td>
                <ConfidenceBadge value={a.confidence} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
