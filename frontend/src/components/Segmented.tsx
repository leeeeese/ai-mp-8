import { cn } from '@/lib/cn';

interface Props<T extends string> {
  label: string; // 스크린리더용 그룹 이름
  options: { value: T; label: string }[];
  value: T | null;
  onChange: (v: T) => void;
  disabled?: boolean;
}

/** 세그먼트 컨트롤 (예: SM 토글 상품 | DBM). 선택값에 glow */
export function Segmented<T extends string>({ label, options, value, onChange, disabled }: Props<T>) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-full border border-border bg-surface-2 p-0.5">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            disabled={disabled}
            onClick={() => onChange(o.value)}
            className={cn(
              'min-h-[32px] rounded-full px-3 text-[13px] font-medium transition-colors disabled:cursor-not-allowed',
              on ? 'bg-accent text-accent-fg shadow-glow' : 'text-muted hover:text-text',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
