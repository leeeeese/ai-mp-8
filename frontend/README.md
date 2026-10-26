# impact-agent UI

SM 영향도 분석 Agent 웹 UI (React 18 + TypeScript + Vite). 백엔드 연동 전이라 API 는 MSW mock 으로 동작한다.
화면 설계: [../docs/ui_design.md](../docs/ui_design.md)

## 실행

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (mock API)
npm run build      # 타입 검사 + 빌드 → dist/
```

- `VITE_USE_MOCK=false npm run dev` 로 실제 API·SSE(`/api/...`)를 호출한다 (Vite proxy 설정은 백엔드 주소가 정해지면 추가).
- 사이드바 하단 "mock 사용자" 에서 유저(DBM) / 관리자(상품·DBM) 를 바꿔 권한별 화면을 확인한다.
- Node.js 20 이상.

## 구조

```
src/
├── api/        types.ts (API 타입), client.ts (TanStack Query 훅), stream.ts (Agent 진행 SSE / mock 타이머)
├── mocks/      MSW 핸들러·메모리 저장소, data/ (시나리오 결과, 그래프 샘플 = data/graph 의 tos·swing 노드·엣지, 원문 제외)
├── store/      theme (라이트/다크), auth (mock SSO·권한), ui
├── components/ 글라스·오로라 공통 요소, 사이드바, Agent 진행 패널, 답변 카드, 판정표, 그래프 미리보기
├── pages/      Home, Session, Report, Graph, Admin
├── styles/     테마 토큰 (CSS 변수)
└── config.ts   루프 한도 등 설정값 (TBD → 백엔드 상수와 맞춤)
```

- 폰트는 `@fontsource` 로 로컬 번들 (외부 CDN 없음).
- 권한에 따른 메뉴·버튼 숨김은 편의 기능이고, 최종 권한 검증은 백엔드에서 한다.
- mock 그래프 샘플 갱신: 저장소 루트에서 `python scripts/build_graph.py && python scripts/export_ui_graph_sample.py`
