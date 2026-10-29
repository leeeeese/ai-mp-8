# API 설계서: impact-agent 전체 API (UI 백엔드)

> 범위: UI 가 호출하는 전체 API (세션 · 진행 SSE · KPI · 적재 현황 · 관리(권한)). 구현: `src/impact_agent/api/` (FastAPI). 계약(요청·응답 타입)은 `frontend/src/api/types.ts` · 목업 `frontend/src/mocks/handlers.ts` 와 같다. 화면별 사용처는 [ui_design.md](ui_design.md) 7장. 기준일 2026-10-29.

## 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-29 | 최초 작성. 전체 API 목록 · 응답 코드 규칙 · 엔드포인트 상세. 세션·KPI·적재 현황 구현, 관리(권한) 501 |

## 1. 개요

| 항목 | 내용 |
|---|---|
| 기술 | FastAPI · uvicorn · Pydantic v2 (요청 검증) · SSE (`text/event-stream`) |
| 기본 주소 | `http://localhost:8000` (프론트는 Vite 프록시로 `/api` 를 전달) |
| 형식 | 요청·응답 모두 JSON (UTF-8). SSE 만 `text/event-stream` |
| 인증 | 없음 (PoC). 사용자·권한은 프론트 mock 사용자로만 구분. 401·403 은 인증 도입 때 추가 (5장) |
| 저장 | 세션·분석 결과: 메모리 (재시작 시 사라짐) / KPI 기록: `outputs/kpi/records.json` / 재적재 요청: `outputs/graph/reload_requests.json` / 그래프: Neo4j 읽기 전용 |
| 동시성 | Supervisor 하나를 공유 → 분석은 한 번에 하나씩 순서대로. 분석은 백그라운드 스레드, 진행은 SSE |

### 파일 구성

| 파일 | 역할 |
|---|---|
| `api/app.py` | 앱 생성(`create_app`), 세션·KPI 라우트, 정답셋 매칭, 관리(권한) API 501 처리 |
| `api/runner.py` | 에이전트 래퍼(`ProgressAgent`) → 진행 이벤트, 세션 기록(`SessionRecord`), JSON 목록 저장소(`KpiStore`), 진행 로그 |
| `api/views.py` | `AgentState` → UI 응답 모양 (`answer_view`, `report_view`, `kpi_record`) |
| `api/graph.py` | 적재 현황 라우터(`graph_router`), Neo4j/CSV 그래프 소스, 품질 점검·검색·2 hop·master 대비 |

## 2. 엔드포인트 목록

| 메서드 · 경로 | 설명 | 성공 | 오류 |
|---|---|---|---|
| `GET /api/sessions` | 세션 목록 (최근 순) | 200 | - |
| `POST /api/sessions` | 세션 생성 + 분석 시작 (백그라운드) | 200 | 422 |
| `GET /api/sessions/{id}` | 세션 조회 (메시지·답변) | 200 | 404 |
| `POST /api/sessions/{id}/messages` | 후속 입력 → 재분석 시작 | 200 | 404 · 409 · 422 |
| `POST /api/sessions/{id}/star` | 즐겨찾기 토글 | 200 | 404 |
| `GET /api/sessions/{id}/report` | 복잡 요건 결과물 | 200 | 404 |
| `PATCH /api/sessions/{id}/checklist/{itemId}` | 체크리스트 답변 → 결과물 갱신 | 200 | 404 · 409 · 422 |
| `GET /api/sessions/{id}/stream` | 진행 이벤트 (SSE) | 200 | 404 |
| `GET /api/kpi/records` | KPI 기록 (최근 순) | 200 | - |
| `DELETE /api/kpi/records` | KPI 기록 전체 초기화 | 200 | - |
| `GET /api/graph/status?sm=` | 적재 현황 | 200 | 422 |
| `GET /api/graph/search?sm=&q=` | 노드 검색 | 200 | 422 · 503 |
| `GET /api/graph/neighbors?sm=&nodeId=&hops=` | 2 hop 그래프 | 200 | 404 · 422 · 503 |
| `POST /api/graph/reload-requests` | 재적재 요청 생성 | 200 | 422 |
| `/api/admin/*` (GET·POST·PATCH·DELETE) | 관리(권한): 사용자·권한 변경, 재적재 승인·반려, 감사 로그 (미구현) | - | 501 (관리(권한) 미구현) |

## 3. 응답 코드 설계

### 3-1. 원칙

1. **성공은 200 하나로 통일한다.** 생성(`POST /api/sessions`, `POST …/reload-requests`)도 201 대신 200. 프론트 `request()` 가 `res.ok` 만 보고, 목업(MSW)도 200 으로 응답하므로 계약을 하나로 맞춘다.
2. **오류는 "누가 고쳐야 하는가"로 나눈다.** 요청이 틀렸으면 4xx(프론트·사용자), 서버가 못 하면 5xx(백엔드·인프라).
3. **오류 본문은 FastAPI 기본 형식** `{"detail": "..."}`. 4xx·501·503 은 화면에 그대로 보여 줄 수 있는 한국어 문장으로 쓴다 (422 만 FastAPI 가 만드는 검증 목록).
4. **화면을 그릴 수 있으면 오류로 만들지 않는다.** 예: Neo4j 연결이 끊겨도 `GET /api/graph/status` 는 200 + `connected: false` 로 "연결 끊김" 배지를 그리게 한다. 데이터가 없으면 빈 목록(200)이지 404 가 아니다 (`GET /api/sessions`, `GET /api/kpi/records`).
5. **분석 실패는 HTTP 오류가 아니다.** 분석은 요청이 끝난 뒤 백그라운드에서 돌기 때문에, 실패하면 세션에 "분석 중 오류가 발생했습니다: …" 메시지를 남기고 SSE 를 정상 종료한다 (서버 터미널에 traceback 로그).

### 3-2. 사용하는 코드

| 코드 | 이름 | 의미 (이 API 에서) | 언제 | 프론트 처리 |
|---|---|---|---|---|
| **200** | OK | 성공 | 모든 정상 응답 (생성 포함) | 응답 본문 사용 |
| **404** | Not Found | 요청한 **대상이 없다** | 없는 세션 ID / 단순 요건 세션의 결과물 / 없는 체크리스트 항목 / 해당 영역에 없는 노드 ID | 안내 문구 표시. 결과물 404 는 "복잡 요건 결과물이 없습니다" (단순 응답이면 정상) |
| **409** | Conflict | 요청은 맞지만 **지금 상태와 충돌**한다 | 분석 중(`status: running`)인 세션에 후속 입력·체크리스트 답변 | 분석이 끝난 뒤 다시 시도 (UI 는 분석 중 입력을 막아 평소엔 안 생김) |
| **422** | Unprocessable Entity | 요청 **형식·값이 틀렸다** (Pydantic 검증 실패, FastAPI 자동) | `smPart` 가 `prod`·`dbm` 외 값, `kind` 가 허용 목록 밖, 필수 필드 누락, `sm` 쿼리 값 오류, `nodeId` 누락 | 개발 오류 (UI 는 정해진 값만 보냄). 본문 `detail` 에 어느 필드가 틀렸는지 목록 |
| **501 (관리(권한))** | Not Implemented | 경로는 계약에 있지만 **아직 구현하지 않았다**. 권한 거부(401·403)가 아니라 미구현 | `/api/admin/*` (관리(권한) API) | 관리 화면이 비어 보임. 404 와 구분해 "없는 기능"이 아니라 "만들 예정"임을 알린다 |
| **503** | Service Unavailable | 서버는 살아 있지만 **의존 서비스(Neo4j)에 연결할 수 없다** | `GET /api/graph/search`, `GET /api/graph/neighbors` 에서 그래프 저장소 연결 실패 | 잠시 후 재시도. `docker compose up -d` 로 Neo4j 확인 |
| 500 | Internal Server Error | 예상하지 못한 서버 오류 (직접 내지 않음, FastAPI 기본) | 코드 버그 | 서버 터미널 traceback 확인 |

**404 · 409 · 422 를 나누는 기준**

| 상황 | 코드 | 이유 |
|---|---|---|
| `GET /api/sessions/없는ID` | 404 | 대상 자체가 없음 |
| 분석 중인 세션에 `POST …/messages` | 409 | 대상은 있고 요청도 맞지만, 끝난 뒤에만 받을 수 있음 (같은 요청을 나중에 보내면 성공) |
| `POST …/messages` 에 `kind: "foo"` | 422 | 요청 자체가 틀림 (언제 보내도 실패) |
| 단순 세션에 `GET …/report` | 404 | 결과물은 복잡 요건에서만 생김. 상태가 바뀌어도(재분석 전) 생기지 않으므로 409 가 아님 |

### 3-3. 쓰지 않는 코드와 이유

| 코드 | 이름 | 쓰지 않는 이유 | 도입 시점 |
|---|---|---|---|
| 201 | Created | 성공 코드를 200 으로 통일 (3-1 원칙 1) | 외부 공개 API 로 바꿀 때 검토 |
| 204 | No Content | 삭제(`DELETE /api/kpi/records`)도 지운 건수 `{deleted}` 를 돌려줘야 함 | - |
| 400 | Bad Request | 형식 오류는 FastAPI 가 422 로 처리. 따로 검사하는 업무 규칙 오류가 아직 없음 | 업무 규칙 검증(예: 빈 요청 문장 거부)이 생기면 400 |
| **401** | Unauthorized | **"누구인지 모른다"** (로그인 안 함·토큰 없음·만료). PoC 는 인증이 없음 | 사내 SSO 연동 시. 토큰 없거나 만료 → 401 → 프론트가 로그인 화면으로 이동 |
| **403** | Forbidden | **"누구인지는 알지만 권한이 없다"**. 지금은 권한을 프론트(`useAuth().can()`)에서만 숨김 | 인증 도입 시 백엔드 검증: 다른 SM 파트 세션 조회, 유저가 `/api/admin/*` 호출, 재적재 승인 → 403 |
| 429 | Too Many Requests | 분석은 한 번에 하나씩 대기열처럼 순서대로 처리 (거부하지 않음) | 다중 사용자 운영 시 |

> **401 과 403 의 차이**: 401 은 "로그인부터 하세요"(신원 확인 실패), 403 은 "로그인은 됐지만 이 작업은 못 합니다"(권한 부족). 예를 들어 김유저(유저 · DBM)가 상품 세션을 열면 403, 로그인하지 않고 열면 401.

## 4. 엔드포인트 상세

### 4-1. 세션

**`POST /api/sessions`** 세션 생성 + 분석 시작

```json
// 요청
{ "smPart": "dbm", "inputType": "requirement", "source": "mail", "text": "요청 문장", "files": [] }
// 응답 200
{ "sessionId": "2d6046072b6547018038cf22dbcac3ba" }
```

| 필드 | 값 | 비고 |
|---|---|---|
| `smPart` | `prod` \| `dbm` | 백엔드 `SmPart.PRODUCT` / `MARKETING`, 탐색 영역 swing / tos |
| `inputType` | `requirement` \| `ops_inquiry` | 요건서 / 운영 문의 |
| `source` | `mail` \| `itsd` \| `etc` | 출처 |
| `text` | 문자열 | 정답셋 `eval/ground_truth/*.json` 의 `request_text` 와 같으면(공백 정규화) 정답을 붙여 KPI 품질 지표까지 계산 |

- 응답은 분석을 기다리지 않고 바로 온다. 진행은 `GET …/stream`, 결과는 `GET /api/sessions/{id}`.

**`POST /api/sessions/{id}/messages`** 후속 입력

```json
{ "kind": "reanalysis", "text": "상세 분석 권장 사유 / ..." }
```

| `kind` (UI) | 백엔드 후속 입력 | 흐름 |
|---|---|---|
| `answer` | `business_answer` | Composer 가 결과물 갱신 |
| `query_result` | `query_result` | 조회 결과 반영 |
| `change` | `change_analysis` | Planner 부터 다시 |
| `chat` | `change_analysis` (가안) | 대응 유형이 없어 변경 분석으로 |
| `reanalysis` | `reanalysis` | "상세 분석 요청" 버튼. 같은 세션을 복잡 요건으로 다시 분석 |

- 응답 200: 갱신된 `Session` (`status: running`). 분석 중이면 409.

**`GET /api/sessions/{id}/report`** 복잡 요건 결과물

- 응답: `{ version, versions[], feasibility, difficulty, scheduleUncertainty, areas[], scheduleFactors[], limits[], checklist[] }`
- `version` 쿼리는 받지만 지금은 v1 하나만 돌려준다.
- 단순 응답 세션이면 404 (결과물 없음).

**`PATCH /api/sessions/{id}/checklist/{itemId}`** 체크리스트 답변

```json
{ "answer": "타사회선은 LINE_DIV_CD = '02'", "status": "confirmed" }
```

- `confirmed` + 답변 → 항목 owner 에 따라 `business_answer` / `developer_opinion` 후속 입력으로 Composer 가 결과물을 갱신한다 (응답은 갱신이 끝난 뒤).
- `unconfirmed` → 결과물의 항목 상태만 되돌린다.
- 응답 200: `ChecklistItem { id, owner, question, status, answer? }`.

### 4-2. 진행 SSE (`GET /api/sessions/{id}/stream`)

```
data: {"node": "planner", "status": "running"}

data: {"node": "planner", "status": "done", "elapsedMs": 12345, "detail": "단순 · 항목 2개",
       "counters": {"toolCalls": 0, "exploreLoops": 0, "replans": 0}}

data: {"node": "retriever", "status": "done", ..., "evidence": [{"nodeId": "SCR:TOSCP0210M", "file": "...", "location": "L40-57"}]}

: keepalive
```

| 필드 | 내용 |
|---|---|
| `node` | `supervisor` · `planner` · `explorer` · `retriever` · `simulator` · `verifier` · `composer` (KPI 는 진행 패널에 넣지 않음) |
| `status` | `running` · `done` · `skipped` (단순 flow 의 Simulator · Verifier) |
| `elapsedMs` · `detail` · `counters` | 완료 이벤트에만. `counters` 는 한도 미터 (도구 호출 n/20, 왕복 n/3, 재계획 n/2) |
| `evidence` | Retriever 완료 이벤트에만 (최대 20) |

- 현재 라운드의 이벤트를 처음부터 다시 보낸 뒤 새 이벤트를 이어 보낸다 (새로고침해도 진행 패널 복원).
- 15초 동안 이벤트가 없으면 `: keepalive` 주석 줄 (프록시 타임아웃 방지).
- `supervisor done` 을 보낸 뒤 스트림을 닫는다.
- 같은 이벤트가 서버 터미널에도 한 줄씩 찍힌다: `[세션ID 8자] planner    done    12.3s · 단순 · 항목 2개`.

### 4-3. KPI

| API | 응답 |
|---|---|
| `GET /api/kpi/records` | `KpiRecord[]` (최근 순). 필드는 백엔드 `KpiOutput` 과 같은 snake_case + 세션 정보 + 합산용 `counts{tp, fn, fp, critical_missed, critical_total}` |
| `DELETE /api/kpi/records` | `{ "deleted": n }`. 세션·분석 결과는 지우지 않음 |

### 4-4. 적재 현황 (`api/graph.py`)

공통: `sm` = `dbm`(tos) | `prod`(swing), 기본 `dbm`. 그래프는 Neo4j 에서 읽기만 하고, 영역별 그래프를 30초 캐시한다 (검색창 입력마다 전체를 다시 읽지 않게).

**`GET /api/graph/status?sm=`** → `GraphStatus`

| 필드 | 계산 |
|---|---|
| `connected` | Neo4j 조회 성공 여부 (실패 시 200 + `false`, 나머지 0·빈 목록) |
| `nodes` · `rels` | 영역 노드 수 · 그 노드에서 나가는 관계 수 |
| `labels[]` | 라벨별 노드 수 + 공통 역할 (Screen·Batch → Entry, Controller·TP → EntryHandler, …) |
| `sourceMapRate` | `source_file` 이 실제 소스 루트(`SOURCE_ROOT_<영역>`)에 있는 노드 비율 (0~1) |
| `checks[]` | 고립 노드 / columns 누락 READS·WRITES / source_file 누락 / 정형 메타 미적재(Column 제외) / 미제공 소스 영역(Batch step 의 `PROC` 프로그램이 적재되지 않음) |
| `masterSync` | 아래 "master 대비" |
| `history[]` | `:LoadMeta` 목록 (최근 순). `result` = 적재 경고가 있으면 `warning` |

**master 대비 (소스 지문 비교)**: `data/` 는 git 밖이라 커밋 대신 파일 해시로 비교한다.

1. `python scripts/build_graph.py --load` 가 적재할 때 영역별 `:LoadMeta {domain, loaded_at, files[], hashes[](sha1), fingerprint, nodes, rels, warnings}` 를 남긴다. `:Node` 가 아니라서 재적재해도 지워지지 않는다 (= 적재 이력).
2. API 가 현재 `SOURCE_ROOT_<영역>` 파일의 sha1 을 계산해 최근 `:LoadMeta` 와 비교한다.

| 필드 | 값 |
|---|---|
| `loadedCommit` / `masterCommit` | 적재 시점 / 현재 소스 지문 앞 7자 |
| `behindCommits` | 지문이 다르면 1, 같으면 0 (커밋 수가 아님) |
| `pendingFiles` | 바뀐·추가된 파일, 삭제된 파일은 `경로 (삭제)` |
| `percent` | (전체 파일 − 미반영) / 전체 × 100 |
| `loadedAt` | 최근 적재 시각 |

- `:LoadMeta` 가 없으면(이 기능 전 적재) `percent: 0`, `loadedCommit: "-"` → 한 번 다시 적재한다.

**`GET /api/graph/search?sm=&q=`** → `{ id, name, label }[]` (최대 12)

- 순위: node_id 일치(접두어 `TBL:` 등 포함·제외) > node_id 앞부분 > node_id 포함 > 이름 포함 > 설명 포함. 같은 순위면 Column 을 뒤로.

**`GET /api/graph/neighbors?sm=&nodeId=&hops=2`** → `{ center, nodes[+hop], edges[] }`

- 방향 무시 BFS, 노드마다 처음 닿은 hop. `hops` 는 1~3 으로 제한.
- 노드 속성은 `scripts/export_ui_graph_sample.py` 와 같은 것만 (소스 원문 `code` 제외).
- 해당 영역에 없는 `nodeId` → 404.

**`POST /api/graph/reload-requests`**

```json
// 요청
{ "sm": "dbm", "scope": "online/java/com/tmk/tos/cp/msg", "reason": "CampMsgService 수정분 반영" }
// 응답 200
{ "id": "rd9f2e76e", "sm": "dbm", "scope": "...", "reason": "...", "requestedBy": "현재 사용자",
  "requestedAt": "2026-10-29T21:56:23+09:00", "status": "pending" }
```

- 요청만 저장한다 (승인·실행은 관리자 → 적재 파이프라인). 인증이 없어 `requestedBy` 는 목업과 같은 `현재 사용자`.

## 5. 인증 · 권한 도입 시 (TBD)

| 단계 | 내용 |
|---|---|
| 인증 | 사내 SSO 토큰 검증 미들웨어. 토큰 없음·만료 → **401** |
| 권한 | 기본: 자기 SM 파트 세션·적재 현황 조회, 재적재 요청 / 슈퍼: 전체 세션, `/api/admin/*`, 재적재 승인 → 위반 시 **403** |
| 세션 목록 | `GET /api/sessions` 를 사용자 SM 파트로 필터 (지금은 프론트에서만 필터) |
| `requestedBy` · 감사 로그 | 토큰의 사번·이름으로 기록 |

## 6. 테스트

| 테스트 | 내용 |
|---|---|
| `tests/test_api.py` | 세션·결과물·체크리스트·SSE·KPI·501(관리(권한)) (가짜 Planner·Explorer·Retriever, LLM·Neo4j 없음) |
| `tests/test_graph_api.py` | 적재 현황 (작은 CSV 그래프 + 임시 소스 루트): 집계·품질 점검·master 대비(변경·추가·삭제)·검색 순위·2 hop·404·재적재 요청·연결 끊김(200 + `connected:false`, 검색 503) |
| 시나리오 | Neo4j + LLM 으로 UI 에서 tos SC-003 · swing L-4 실행 (결과: [api_integration_test.md](api_integration_test.md)) |

```bash
PYTHONPATH=src pytest tests/test_api.py tests/test_graph_api.py -q
```
