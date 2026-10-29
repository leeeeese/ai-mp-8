# 화면 설계서: impact-agent 웹 UI

> 구현: `frontend/` (React 18 + TypeScript + Vite, API 는 MSW mock). 기준일 2026-10-25.


## 1. 개요

| 항목 | 내용 |
|---|---|
| 목적 | SM 담당자가 요건·운영 문의를 넣고, Agent 분석 과정과 결과(레포트·체크리스트)를 확인하며, 그래프 적재 현황을 점검 |
| 사용자 | 유저(기본 권한), 관리자(기본 + 슈퍼 권한). 사내 SSO 가정 |
| 기술 | React 18 · TypeScript · Vite / Tailwind CSS + CSS 변수 토큰 / Zustand(UI 상태) · TanStack Query(서버 데이터) / React Router / SSE(EventSource, mock 은 타이머) / Cytoscape.js / MSW / lucide-react / @fontsource (로컬 폰트) |
| 실행 | `cd frontend && npm install && npm run dev` → http://localhost:5173 |

## 2. 화면 목록

| ID | 화면 | 경로 | 권한 | 캡처 |
|---|---|---|---|---|
| UI-01 | 홈 (빈 상태 시작) | `/` | 기본 | [라이트](ui/01-home-light.png) |
| UI-02 | 분석 채팅 | `/session/:id` | 기본 (자기 SM 파트 세션. 전체는 슈퍼) | [복잡·완료](ui/02-session-light.png), [진행 중·다크](ui/02-session-running-dark.png), [단순](ui/03-simple-light.png) |
| UI-03 | 결과물 | `/session/:id/report` | 기본 | [다크](ui/04-report-dark.png) |
| UI-04 | 적재 현황 | `/graph` | 기본 (재적재 승인은 슈퍼) | [라이트](ui/05-graph-light.png) |
| UI-05 | 관리 | `/admin` | 슈퍼 | [다크](ui/06-admin-dark.png) |

## 3. 디자인 시스템

### 3-1. 컨셉
Dribbble "Forge – AI Agent Code Workspace"(라이트) · "AI Knowledge Base Empty State"(다크) 참고. 채팅·대시보드·관리 화면 모두 같은 컨셉.
- 글라스 카드: 반투명 배경 + `backdrop-filter: blur(16px)` (`.glass`)
- 오로라: 화면 하단 큰 타원 radial-gradient 2개 + blur (`.aurora`, 고정 배경)
- 강조색: 청록 한 계열 (다크에서도 보라로 바뀌지 않음)
- 제목·노드 ID 는 모노스페이스, 본문은 산세리프
- 네온 glow 는 주요 버튼, 진행 중 단계, 선택된 토글, 핵심 KPI 에만

### 3-2. 테마 토큰 (`src/styles/index.css`, `:root[data-theme="light|dark"]`)

| 토큰 | 라이트 | 다크 | 용도 |
|---|---|---|---|
| `--bg` | `#FFFFFF` | `#050608` | 바탕 |
| `--surface` | `rgba(255,255,255,.85)` | `rgba(22,24,30,.72)` | 글라스 카드 |
| `--surface-2` | `#F7F8FA` | `rgba(255,255,255,.04)` | 칩·입력·보조 |
| `--border` | `#E6E8EC` | `rgba(255,255,255,.09)` | 테두리 |
| `--text` / `--muted` | `#16181D` / `#5F6573` | `#ECEEF4` / `#959AAB` | 글자 |
| `--accent` / `--accent-fg` | `#0B7A87` / `#FFFFFF` | `#7FE3EC` / `#04151A` | 강조 / 강조 위 글자 |
| `--accent-soft` | `rgba(11,122,135,.08)` | `rgba(127,227,236,.10)` | 선택 메뉴·사용자 메시지 배경 |
| `--accent-glow` | `0 4px 12px rgba(11,122,135,.25)` | `0 0 14px rgba(127,227,236,.55)` | glow |
| `--aurora-1` / `-2` | 민트 / 하늘 | 청록 / 남청 | 오로라 |
| `--ok` / `--est` / `--warn` | `#1E7A46` / `#8A5A00` / `#B4441F` | `#6EE7A8` / `#F5C451` / `#FFB27A` | 확인 / 추정 / 경고 |

- 폰트: 제목·노드 ID `JetBrains Mono`, 본문 `Inter` + `Noto Sans KR` (모두 `@fontsource` 로컬 번들)
- 모서리: 카드 16px, 칩 8px, 토글·배지·버튼 999px
- 테마: 상단 바 우측 해/달 토글. 저장값(localStorage `impact-theme`) → 없으면 OS 설정. 첫 페인트 전에 적용(깜빡임 없음)

### 3-3. 공통 컴포넌트 (`src/components`)

| 컴포넌트 | 설명 |
|---|---|
| `Aurora`, `OrbitLogo` | 오로라 배경, 빛나는 궤도 로고 타일 |
| `PageHeader` | 제목 · 배지 · 우측 액션 · 테마 토글 (줄바꿈 허용) |
| `Segmented` | 세그먼트 컨트롤 (`role=radiogroup`), 선택값 glow |
| `ChipSelect` | 칩 모양 네이티브 select |
| `Badge`, `ConfidenceBadge` | 상태 배지, 근거 `● 확인` / `◐ 추정` |
| `AgentProgress` | Agent 진행 패널 (단계·경과·한도 미터·근거) |
| `AnswerCard`, `AreaTable`, `QueryBlock` | 답변 카드, 영역별 판정표, 복사용 확인 쿼리 |
| `GraphPreview` | Cytoscape 2 hop 그래프 (색 = 공통 역할) |
| `MasterSyncCard` | 적재 현황 · master 대비 카드 |
| `Modal`, `Tabs`, `RequirePermission` | 네이티브 dialog, 탭, 권한 가드 |

## 4. 레이아웃

```
┌──────────────┬──────────────────────────────────────────────┐
│ 사이드바 240px│ 상단 바: 제목 · 배지 · 액션 · 테마 토글       │
│ (글라스)      ├──────────────────────────────────────────────┤
│ 로고          │                                              │
│ 홈            │ 본문 (유동 폭)                                │
│ 적재 현황     │                                              │
│ 결과물        │                                              │
│ 관리 (관리자) │                                              │
│ ─ 즐겨찾기    │                                              │
│ ─ 전체 세션   │                                              │
│   오늘/어제/… │                                              │
│ [master 대비] │                                              │
│ mock 사용자   │                                    (오로라)  │
└──────────────┴──────────────────────────────────────────────┘
```

- 사이드바: 메뉴, 세션(즐겨찾기 + 날짜별 트리, 분석 중 점 표시), 하단 "적재 현황 · master 대비" 카드, mock 사용자 전환
- 일반 유저는 자기 SM 파트 세션만 보임 (슈퍼 권한은 전체). 최종 필터링은 백엔드
- "결과물" 메뉴는 가장 최근 복잡 분석 세션의 결과물로 이동
- 본문은 가로 넘침을 막고 표는 자체 가로 스크롤

## 5. 화면 상세

### UI-01 홈 (`/`)

```
            [궤도 로고 타일]
        어떤 요건을 분석할까요?
 ┌───────────────────────────────────────┐
 │ 요건서 내용이나 운영 문의를 붙여 넣으세요 │
 │                                       │
 │ [상품|DBM] [요건서▾] [메일▾] [📎]   (↑) │
 └───────────────────────────────────────┘
      [예시 질문 칩] [예시 질문 칩]
```

| 요소 | 동작 |
|---|---|
| SM 토글 `상품 \| DBM` | **필수**. 접근 권한이 있는 파트만 선택 가능. 파트가 하나면 자동 선택. 미선택 시 안내 문구 |
| 입력 유형 | 요건서 / 운영 문의 |
| 출처 | 메일 / ITSD / 기타 |
| 📎 첨부 | 파일명 칩으로 표시, 칩에서 해제 |
| 전송 (↑) | SM 선택 + 내용이 있을 때 활성. `⌘/Ctrl + Enter` 도 전송. `POST /api/sessions` → `/session/:id` 이동 |
| 예시 칩 | 누르면 내용·SM·유형을 채움 (권한 있는 SM 의 예시만) |

### UI-02 분석 채팅 (`/session/:id`)

```
┌ 상단 바: 세션 제목 [SM DBM] [복잡] [분석 중]            ☆ ☾ ┐
├──────────────────────────────────────┬──────────────────────┤
│              [사용자 메시지 (우측)]   │ Agent 진행           │
│ ┌ Agent 답변 카드 ──────────────────┐ │ ○ Supervisor   진행  │
│ │ Planner 판정                       │ │ ✓ 1 Planner  완료 1.3s│
│ │ ▸ 분석 과정 (접힘)                  │ │ ◌ 2-1 Explorer ⇄ 진행 │
│ │ 요약 결론                          │ │ ○ 2-2 Retriever ⇄    │
│ │ 영역별 판정표                      │ │ ○ 2-3 Simulator      │
│ │ [확인 필요 7건]                    │ │ ○ 2-4 Verifier       │
│ │ [레포트·체크리스트][회신 메일][PDF] │ │ ○ 3 Composer         │
│ │ [현업 답변 입력]                   │ │                      │
│ └───────────────────────────────────┘ │ 도구 호출   3/20      │
│ ┌ 후속 입력 ────────────────────────┐ │ 왕복        0/3       │
│ │ [추가 질문][조회 결과][현업 답변][변경 분석]  🔒SM DBM 고정 │ 재계획      0/2       │
│ │ 입력…                         (↑) │ │ 근거 목록            │
│ └───────────────────────────────────┘ │                      │
└──────────────────────────────────────┴──────────────────────┘
```

- 진행 패널에는 KPI Agent 를 넣지 않는다. KPI 는 서비스를 만든 뒤 잘 되는지 확인하는 개발·운영용 측정이라 사용자 세션 화면에 보일 필요가 없다 (Composer 이후 PoC 평가, 별도 KPI 페이지).

| 영역 | 내용 |
|---|---|
| 상단 바 | 세션 제목, SM 배지, 단순/복잡 배지, 분석 중 배지(glow), 즐겨찾기, 테마 토글 |
| 사용자 메시지 | 오른쪽, accent 옅은 배경. 후속 입력이면 유형 표시 |
| 답변 카드 (복잡) | Planner 판정 → 분석 과정(접힘) → 요약 결론 → 영역별 판정표(영역 / 영향·경로(노드 ID) / 요청안·대안 가능성·난이도 / 근거 확인·추정) → 확인 필요 N건 → 액션 |
| 답변 카드 (단순) | 결론 + 근거 표(내용·근거·위치·확인) + **복사 가능한 확인 쿼리**(읽기 전용, 사용자가 직접 실행) |
| 상세 분석 요청 | 단순 응답에서 상세 분석이 권장되면(`detail_analysis_recommended`) "상세 분석 권장" 박스에 사유(`detail_analysis_reasons`, 노드 ID 포함)를 보여 주고, "상세 분석 요청" 버튼 한 번으로 사유를 `reanalysis` 후속 입력으로 보낸다 → 같은 세션이 복잡 요건으로 다시 돈다. 요청 후·분석 중에는 버튼을 막는다. `reanalysis` 는 입력창 유형 선택에는 없다 ([planner_detail_analysis_to-be.md](planner_detail_analysis_to-be.md)) |
| Agent 진행 패널 | SSE 로 실시간 갱신. 단계별 대기/진행/완료/생략, 경과 시간, 한도 미터(도구 호출 n/20, Explorer ⇄ Retriever 왕복 n/3, 재계획 n/2, 한도 도달 시 경고색), 근거 목록(노드 ID · 파일 · 라인). `aria-live=polite` |
| 후속 입력 | 유형 칩 `추가 질문 / 조회 결과 입력 / 현업 답변 입력 / 변경 분석 요청`, SM 파트 고정 표시. 분석 중에는 비활성 |

- 끝난 세션은 진행 패널을 완료 상태로 그린다 (단순이면 Simulator·Verifier 생략).
- 메시지가 늘면 맨 아래로 스크롤.

### UI-03 결과물 (`/session/:id/report`)

| 영역 | 내용 |
|---|---|
| 상단 바 | 세션 제목, SM 배지, 버전 선택(v1 최초 분석 / v2 현업 답변 반영 …) |
| KPI 3개 | 반영 가능성(강조·glow), 전체 난이도, 일정 불확실성 |
| 영역별 판정표 | v2 이상이면 바뀐 행 강조 + "변경" 표시 |
| 일정 영향 요인 / 분석 한계 | 목록 |
| 액션 | 회신 메일 초안, PDF 저장, **개발 전달용 정리본** (미확정 항목이 남으면 비활성 + 남은 건수 안내) |
| 체크리스트 (우측, 2xl 미만은 아래) | 진행률 바(확정/전체), "업무 확정 필요" / "개발 담당자 확인" 그룹, 항목별 확정 토글, 인라인 답변 입력(저장하면 확정), 바뀐 항목 테두리 강조 |

### UI-04 적재 현황 (`/graph`)

| 영역 | 내용 |
|---|---|
| 상단 바 | Neo4j 연결 상태, 최종 적재 시각(넓은 화면), 상품/DBM 토글 (사이드바 master 대비 카드도 같은 기준으로 바뀜) |
| KPI 타일 4개 | **master 반영률**(강조, 적재 커밋 → master 커밋 · N커밋 뒤), 노드/관계 수, **소스 원문 매핑률**(source_file 로 원문 조회 가능한 노드 비율), 점검 필요 건수 |
| 2 hop 미리보기 | node_id·이름 검색(콤보박스) → 시작 노드 + 1·2 hop 그래프. 노드 색 = 공통 역할(Entry/EntryHandler/Logic/DataAccess/Table/Column), 시작 노드는 테두리. 컬럼 노드는 기본 숨김(토글), 2 hop 노드 이름은 선택 시 표시 |
| 노드 상세 | node_id, 라벨, 역할, hop, source_file·라인, 설명, 공통 노드 여부, 들어오는/나가는 연결(via, 접근 컬럼) |
| 노드 라벨별 막대 | Screen · Batch · Controller · TP · Service · Module · SQL · DBIO · Table · Column (역할 표시) |
| 품질 점검 | 고립 노드, columns 누락 READS/WRITES, source_file 누락, 정형 메타 미적재(설명·코멘트 없음), 미제공 소스 영역(Job 은 있으나 프로그램 소스 없음). 항목별 펼침 |
| master 미반영 파일 | 사이드바 카드에서 `/graph#pending` 으로 바로 이동 |
| 적재 이력 + 재적재 요청 | 시각·커밋·노드·관계·실행·결과. "재적재 요청"은 모달(범위·사유)로 **요청만 생성** (승인·실행은 관리자, 프론트·Agent 는 그래프에 쓰지 않음) |

### UI-05 관리 (`/admin`, 슈퍼 권한)

| 탭 | 내용 |
|---|---|
| 사용자 | 추가 폼(사번 `A12345` 형식, 이름, 역할 유저/관리자, 접근 SM 파트 체크) / 목록(사번·이름, 역할, 권한 기본 또는 기본+슈퍼, SM 파트, 최근 접속, 상태, 편집 모달) |
| 권한 구성 | 기본 권한 / 슈퍼 권한 항목 |
| 재적재 요청 | 요청 목록, 대기 건 승인·반려 (감사 로그에 기록) |
| 감사 로그 | 시각, 수행자, 작업, 대상 |

권한이 없으면 "권한이 없습니다" 안내 화면.

## 6. 권한

| 역할 | 권한 |
|---|---|
| 유저 | 기본 |
| 관리자 | 기본 + 슈퍼 |

- 기본: 요건 분석 실행, 결과물 조회·체크리스트 답변, 회신 메일 초안·PDF 저장, 적재 현황 조회, 재적재 **요청**
- 슈퍼: 재적재 승인·실행, 루프 한도·프롬프트 설정, 사용자 추가·권한 변경, 감사 로그 조회, SM 파트 무관 전체 세션 열람
- 프론트는 `useAuth()` 의 `role`·`smParts`·`can(permission)` 으로 메뉴·버튼을 숨김/비활성 (`src/store/auth.ts`). **최종 권한 검증은 백엔드**
- mock 사용자 2명 (사이드바에서 전환, localStorage 저장): 김유저(유저 · DBM), 이관리(관리자 · 상품/DBM)
- Neo4j 쓰기는 적재 파이프라인 전용. 프론트·Agent 는 읽기 API 만

## 7. API (가안, `src/api/types.ts`)

`SmPart = 'prod' | 'dbm'` (백엔드 `product` / `marketing`, 탐색 영역 swing / tos)

| 메서드 · 경로 | 요청 → 응답 |
|---|---|
| `POST /api/sessions` | `{ smPart, inputType, source, text, files? }` → `{ sessionId }` |
| `GET /api/sessions` | → `SessionSummary[]` |
| `GET /api/sessions/:id` | → `Session` (messages, answer) |
| `POST /api/sessions/:id/messages` | `{ kind: 'chat'\|'query_result'\|'answer'\|'change', text }` → `Session` |
| `POST /api/sessions/:id/star` | 즐겨찾기 토글 → `SessionSummary` |
| `GET /api/sessions/:id/stream` (SSE) | `AgentEvent { node, status: running\|done\|skipped, detail, elapsedMs, counters{toolCalls, exploreLoops, replans}, evidence[] }` |
| `GET /api/sessions/:id/report?version=n` | → `Report` (versions, KPI, areas, scheduleFactors, limits, checklist) |
| `PATCH /api/sessions/:id/checklist/:itemId` | `{ answer, status }` → `ChecklistItem` |
| `GET /api/graph/status?sm=` | → `{ masterSync{percent, loadedCommit, masterCommit, behindCommits, pendingFiles, loadedAt}, nodes, rels, sourceMapRate, labels[], checks[], history[] }` |
| `GET /api/graph/search?sm=&q=` | → 노드 후보 `{ id, name, label }[]` (추가) |
| `GET /api/graph/neighbors?sm=&nodeId=&hops=2` | → `{ center, nodes[+hop], edges[] }` |
| `POST /api/graph/reload-requests` | `{ sm, scope, reason }` → `ReloadRequest` |
| `GET/POST/PATCH /api/admin/users` | 사용자 목록·추가·수정 |
| `GET /api/admin/reload-requests`, `POST …/:id/approve\|reject` | 재적재 승인·반려 |
| `GET /api/admin/audit-logs` | 감사 로그 |

- SSE: `VITE_USE_MOCK=false` 면 `EventSource`, 아니면 같은 이벤트 순서를 타이머로 재생하고 끝나면 mock 저장소에 답변을 붙인다 (`src/api/stream.ts`).
- 백엔드 `AgentState` 와의 대응: `planner.complexity` → 배지·Planner 판정, `explorer/retriever` 실행 로그 → 진행 패널, `retriever.evidence` → 근거 목록, `state.tool_call_count`·`explore_loop_count`·`replan_count` → 한도 미터.

## 8. mock 데이터

| 데이터 | 출처 |
|---|---|
| 세션 3건 (SC-001 복잡, SC-006 단순, H-1 상품) | 목업 시나리오 정답셋을 바탕으로 작성한 예시 결과 (`src/mocks/fixtures/scenarios.ts`) |
| 그래프 샘플 | 실제 적재 그래프(`data/graph`)의 tos·swing 노드·엣지, **소스 원문 제외** (`python scripts/export_ui_graph_sample.py`) |
| 품질 점검 수치 | 위 그래프에서 계산 (예: dbm 고립 노드 4개) |
| master 대비·적재 이력·사용자·재적재 요청·감사 로그 | 가상 값 |

mock 저장소는 메모리라 새로고침하면 초기화된다.
