import { ArrowUp, Paperclip, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCreateSession } from '@/api/client';
import { INPUT_TYPE_LABEL, SM_LABEL, SOURCE_LABEL, type InputSource, type InputType, type SmPart } from '@/api/types';
import { ChipSelect } from '@/components/ChipSelect';
import { OrbitLogo } from '@/components/OrbitLogo';
import { PageHeader } from '@/components/PageHeader';
import { Segmented } from '@/components/Segmented';
import { useAuth } from '@/store/auth';

const EXAMPLES: { text: string; sm: SmPart; type: InputType }[] = [
  { text: '캠페인 등록 화면에서 비마케팅 여부를 입력받고 T world 에 같이 전송해 주세요.', sm: 'dbm', type: 'requirement' },
  { text: '캠페인 발송 실패 고객의 실패 사유를 확인하고 싶습니다.', sm: 'dbm', type: 'ops_inquiry' },
  { text: '결합상품 A 를 무선+무선만으로도 가입할 수 있게 해 주세요.', sm: 'prod', type: 'requirement' },
  { text: '구비서류 자동처리 배치 주기를 30분에서 5분으로 단축해 주세요.', sm: 'prod', type: 'requirement' },
];

export function Home() {
  const navigate = useNavigate();
  const { smParts, can } = useAuth();
  const create = useCreateSession();
  const [sm, setSm] = useState<SmPart | null>(smParts.length === 1 ? smParts[0] : null);
  const [inputType, setInputType] = useState<InputType>('requirement');
  const [source, setSource] = useState<InputSource>('mail');
  const [text, setText] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const canSend = !!sm && text.trim().length > 0 && can('analyze') && !create.isPending;
  const submit = async () => {
    if (!canSend || !sm) return;
    const { sessionId } = await create.mutateAsync({ smPart: sm, inputType, source, text: text.trim(), files });
    navigate(`/session/${sessionId}`);
  };

  return (
    <>
      <PageHeader title="홈" />
      <div className="mx-auto flex min-h-[calc(100vh-76px)] max-w-[720px] flex-col items-center justify-center gap-6 px-4 py-10">
        <OrbitLogo />
        <h2 className="text-center font-mono text-2xl font-semibold md:text-[32px]">어떤 요건을 분석할까요?</h2>

        <form
          className="glass w-full p-3 shadow-[0_10px_40px_-20px_rgba(0,0,0,0.3)]"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="request" className="sr-only">
            요건 또는 운영 문의 내용
          </label>
          <textarea
            id="request"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit();
            }}
            rows={4}
            placeholder="요건서 내용이나 운영 문의를 붙여 넣으세요. (⌘/Ctrl + Enter 로 전송)"
            className="w-full resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-text outline-none placeholder:text-muted"
          />
          {files.length > 0 && (
            <ul className="flex flex-wrap gap-1.5 px-1 pb-2">
              {files.map((f) => (
                <li key={f} className="chip">
                  <Paperclip size={12} /> {f}
                  <button type="button" onClick={() => setFiles(files.filter((x) => x !== f))} aria-label={`${f} 첨부 해제`}>
                    <X size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2.5">
            <Segmented
              label="SM 파트 (필수)"
              options={(['prod', 'dbm'] as SmPart[]).map((p) => ({ value: p, label: SM_LABEL[p] }))}
              value={sm}
              onChange={(v) => smParts.includes(v) && setSm(v)}
            />
            <ChipSelect
              label="입력 유형"
              value={inputType}
              onChange={setInputType}
              options={(Object.keys(INPUT_TYPE_LABEL) as InputType[]).map((v) => ({ value: v, label: INPUT_TYPE_LABEL[v] }))}
            />
            <ChipSelect
              label="출처"
              value={source}
              onChange={setSource}
              options={(Object.keys(SOURCE_LABEL) as InputSource[]).map((v) => ({ value: v, label: SOURCE_LABEL[v] }))}
            />
            <button type="button" className="chip min-w-[32px] justify-center" onClick={() => fileRef.current?.click()} aria-label="파일 첨부">
              <Paperclip size={14} />
            </button>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? []).map((f) => f.name)])}
            />
            <button type="submit" className="btn-primary ml-auto h-10 w-10 !px-0" disabled={!canSend} aria-label="분석 시작">
              <ArrowUp size={18} />
            </button>
          </div>
          {!sm && <p className="px-1 pt-2 text-xs text-warn">SM 파트를 선택하세요. 세션이 시작되면 바꿀 수 없습니다.</p>}
          {sm && !smParts.includes(sm) && <p className="px-1 pt-2 text-xs text-warn">이 SM 파트에 접근 권한이 없습니다.</p>}
        </form>

        <div className="flex w-full flex-col items-center gap-2" aria-label="예시 질문">
          {EXAMPLES.filter((e) => smParts.includes(e.sm)).map((e) => (
            <button
              key={e.text}
              type="button"
              className="chip min-w-0 max-w-full cursor-pointer overflow-hidden text-left text-muted hover:text-text"
              onClick={() => {
                setText(e.text);
                setSm(e.sm);
                setInputType(e.type);
              }}
            >
              <span className="truncate">{e.text}</span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
