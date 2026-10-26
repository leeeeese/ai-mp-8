import { ChevronDown } from 'lucide-react';

/** 칩 모양 select (네이티브 select 라 키보드·스크린리더 지원) */
export function ChipSelect<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <label className="chip relative cursor-pointer pr-7">
      <span className="sr-only">{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="cursor-pointer appearance-none bg-transparent pr-1 outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-[color:var(--bg)]">
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown size={14} className="pointer-events-none absolute right-2 text-muted" />
    </label>
  );
}
