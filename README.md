# Impact Analysis Agent

요건(ITSD/메일 등)이나 운영 문의를 받아 **화면 · DB · 인터페이스 · 배치** 영역의 영향도를 분석하는 멀티 에이전트입니다.
모든 에이전트의 입출력은 [`schemas/schemas.json`](schemas/schemas.json)(JSON Schema 2026-10) 기준으로 정의합니다.

## 흐름

```
요건서 + SM 파트 선택 (UserInput)
        │
        ▼
   Supervisor ── 순서·재계획 제어 · 실행 로그 기록 ◀── 재계획 (Verifier: needs_supplement, ≤ MAX_REPLAN)
        │                                                                      │
        ▼                                                                      │
 1. Planner ── 복잡도 · 요건 항목별 탐색 명세(목표 역할 · 방향 · hop 상한)      │
        │ 판정 결과로 이후 flow 결정                                           │
        ▼                                                                      │
 2-1 Explorer ──경로──▶ 2-2 Retriever          (확장 요청 ≤ MAX_EXPLORE_LOOP)   │
   (Neo4j)  ◀─확장 요청─  (소스 직접 조회)                                     │
                              │ 복잡                 │ 단순                     │
                              ▼                      │                          │
                       2-3 Simulator ──▶ 2-4 Verifier ┼─────────────────────────┘
                                              │ pass  │
                                              ▼       ▼
 3. Composer ── 레포트 · 체크리스트 / 회신 메일 초안 · MD/PDF (요청 시)
        ├─▶ 복잡: ComplexReport (영역별 영향·가능성·대안, 업무·개발 확인 체크리스트)
        ├─▶ 단순: SimpleAnswer (요약 결론, 영향 경로·근거, 상세 분석 권장·사유)
        │        └ 사용자 확인(UI) → reanalysis → Supervisor → Planner 가 complex 로 판정 → 2-3 Simulator
        ┆
        ┆ (PoC 평가)
        ▼
 KPI Agent ── [변경] 정밀도 · [변경] 재현율 · 탐지율 (+ 보조) · 시간   ◀── 정답 노드 목록(AnswerKey) + 실행 로그
```

| 에이전트 | 출력 스키마 | 사용 tool |
|---|---|---|
| Planner | `PlannerOutput` | - |
| Explorer | `ExplorerOutput` | `search_graph_nodes`, `traverse_graph`, `get_node_detail` |
| Retriever | `RetrieverOutput` | `read_source` |
| Simulator | `SimulationOutput` | - |
| Verifier | `VerifierOutput` | - |
| Composer | `ComposerOutput` | `save_report` |
| KPI Agent | `KpiOutput` | - (PoC 평가, 규칙 기반, LLM 미사용. Composer 이후 실행) |

## 폴더 구조

```
.
├── schemas/
│   └── schemas.json          # 단일 원천(Single Source of Truth): $defs + constants
├── src/impact_agent/
│   ├── __main__.py           # CLI 진입점
│   ├── config.py             # constants (MAX_EXPLORE_LOOP 등)를 schemas.json에서 로드
│   ├── rules.py              # 코드 판단 기준표 (변경 유형 → 목표 역할·방향·hop, SM → 영역, tool 한도 배분)
│   ├── llm.py                # OpenAI / Azure 호출 (Structured Outputs, seed 고정)
│   ├── state.py              # 에이전트 간 공유 상태(AgentState)
│   ├── trace.py              # 실행 로그 (받은 입력 → 동작 → 근거 → 결과)
│   ├── schemas/              # $defs → Pydantic 모델 (담당 에이전트별 분리)
│   │   ├── enums.py          #   Area, Difficulty, Feasibility, ... (enum 전체)
│   │   ├── inputs.py         #   UserInput, InputMeta, FollowupInput
│   │   ├── planner.py        #   StructuredRequest, OpenItem, PlanStep, PlannerOutput, ReplanFeedback
│   │   ├── explorer.py       #   GraphNode, GraphEdge, StartNodeChoice, UnresolvedItem, ExplorerOutput
│   │   ├── retriever.py      #   Evidence, ReexploreRequest, RetrieverOutput
│   │   ├── simulator.py      #   PathHop, ImpactPath, Alternative, AreaImpact, SimulationOutput
│   │   ├── verifier.py       #   VerifyCheck, VerifierOutput
│   │   ├── kpi.py            #   KpiMetric, AgentRunRecord, AnswerKey, KpiOutput
│   │   ├── composer.py       #   SimpleAnswer, ComplexReport, ChecklistItem, MailDraft, ComposerOutput, ...
│   │   └── tools.py          #   tool 입출력 (SearchGraphNodes*, TraverseGraph*, ReadSource*, SaveReport*)
│   ├── agents/               # supervisor · planner · explorer · retriever · react(ReAct 루프) · simulator · verifier · composer · kpi
│   ├── api/                  # UI 백엔드 API (FastAPI: 세션·SSE·KPI·적재 현황·권한·상세 로그)
│   ├── tools/                # graph(Neo4j/CSV) · source(원문 조회) · report(저장) + TOOL_REGISTRY
│   ├── prompts/              # 에이전트별 시스템 프롬프트 (*.md, _common.md 는 공통 머리말)
│   └── templates/            # Composer markdown 템플릿 (Jinja2)
├── frontend/                 # 웹 UI (React 18 + TypeScript + Vite, MSW 목업)
├── eval/
│   ├── ground_truth/         # 정답셋 16건 (<요건ID>.json, AnswerKey)
│   └── inputs/               # 정답셋 첨부 입력 (SC-006 조회 결과 CSV)
├── tests/                    # 스키마 동기화 · 에이전트별 · Supervisor 루프 · KPI · API · 템플릿 테스트
├── scripts/
│   ├── setup.sh / setup.ps1  # 가상환경 + 패키지 일괄 설치
│   ├── build_graph.py        # data/source → data/graph 생성 (+ --load Neo4j 적재). 파서는 graph/
│   ├── check_ground_truth.py # 정답셋 노드가 그래프에 있는지 검사
│   ├── eval_kpi.py           # 저장된 *.state.json 배치 KPI 평가
│   ├── export_schema.py      # Pydantic 모델 → JSON Schema 재생성 (비교용)
│   ├── export_ui_graph_sample.py  # 프론트 목업용 그래프 샘플
│   ├── YJ/                   # E2E 1회 실행·report (run_all.sh, run_scenarios.py), 가드레일 테스트
│   └── SE/                   # E2E 반복 실험·상류 고정 비교 (run_experiment.py, run_scenarios.py, replay_run.py)
├── docs/                     # 설계서 (아래 문서 목록)
├── requirements.txt          # pip install -r requirements.txt (= .[all,dev])
└── docker-compose.yml        # 로컬 Neo4j (2-1 Explorer)
```

| 문서 | 내용 |
|---|---|
| [docs/YJ_agent_design.md](docs/YJ_agent_design.md) | Agent 설계서: Supervisor · Planner · Explorer · Retriever, E2E 평가 결과 |
| [docs/SE_agent_design.md](docs/SE_agent_design.md) | Agent 설계서: Simulator · Verifier · Composer |
| [docs/SE_kpi-agent-design.md](docs/SE_kpi-agent-design.md) | KPI Agent 설계서 (지표 정의) |
| [docs/architecture.md](docs/architecture.md) | 상수 · 단계별 데이터 흐름 · 분기 규칙 · enum 요약 |
| [docs/ui_design.md](docs/ui_design.md) · [docs/api_design.md](docs/api_design.md) | 화면 설계서 · API 설계서 |
| [docs/mock_data.md](docs/mock_data.md) · [docs/neo4j_guide.md](docs/neo4j_guide.md) | 목업 소스·그래프 구조 · Neo4j 적재·조회 |

## 시작하기

### 요구 사항
- Python 3.11 이상
- (PDF 저장 시) WeasyPrint 시스템 라이브러리
  - macOS: `brew install pango`
  - Ubuntu/Debian: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`
  - Windows: [WeasyPrint 설치 안내](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows) 참고
- (Explorer) Neo4j 5.x 이상. 로컬에서는 Docker Desktop으로 띄웁니다. 아래 [Neo4j 실행 (Docker)](#neo4j-실행-docker) 참고

### 한 번에 설치

```bash
git clone https://github.com/leeeeese/ai-mp-8.git
cd ai-mp-8

# macOS / Linux
./scripts/setup.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

스크립트는 `.venv` 가상환경을 만들고, `requirements.txt`로 모든 패키지를 설치하고, `.env.example`을 복사해 `.env`를 만든 다음 테스트까지 실행합니다.

수동으로 설치할 때는 아래 명령을 차례로 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt     # = pip install -e ".[all,dev]"
cp .env.example .env               # Windows: copy .env.example .env
pytest
```

### Neo4j 실행 (Docker)

Neo4j 가입이나 별도 설치는 필요 없습니다. `docker compose up` 시 `neo4j:5` 이미지가 자동으로 내려받아집니다.

1. `.env`에 비밀번호를 정합니다. 계정은 **최초 실행 시** 이 값으로 생성됩니다.
   ```
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USER=neo4j              # 기본 관리자 계정명, 변경하지 않음
   NEO4J_PASSWORD=<8자 이상>      # 영문·숫자 권장 (/, $ 등 특수문자 피하기)
   ```
2. Docker Desktop을 켠 뒤 컨테이너를 실행합니다.
   ```bash
   docker compose up -d
   docker compose logs -f neo4j    # "Started." 가 보이면 준비 완료
   ```
3. http://localhost:7474 에서 `neo4j` / `.env`의 비밀번호로 로그인해 확인합니다.

| 목적 | 명령 |
|---|---|
| 중지 (데이터 유지) | `docker compose down` |
| 다시 시작 | `docker compose up -d` |
| 완전 초기화 (비밀번호 재설정 포함) | `docker compose down` 후 `rm -rf data/neo4j` |

- 데이터는 `./data/neo4j`에 저장되며 `.gitignore` 대상입니다.
- 목업 데이터(`data/`) 받기, 그래프 적재(`scripts/build_graph.py --load`), 브라우저에서 노드 종류별 색으로 전체 그래프 보기는 [docs/neo4j_guide.md](docs/neo4j_guide.md) 참고. `data/` 는 git 에 없으므로 필요하면 관리자(파트너 모두 해당)에게 zip 을 요청합니다.
- 최초 실행 이후에는 `.env` 비밀번호를 바꿔도 반영되지 않습니다. 브라우저 UI에서 `ALTER CURRENT USER SET PASSWORD FROM '기존' TO '새것'`으로 바꾸거나, 위의 완전 초기화를 하세요.
- APOC 플러그인이 필요하면 `docker-compose.yml`의 `NEO4J_PLUGINS` 주석을 해제합니다.

### 패키지 묶음 (`pyproject.toml`)

| 묶음 | 패키지 | 용도 |
|---|---|---|
| (기본) | pydantic, jinja2, python-dotenv, langgraph | 스키마, 흐름 제어(Supervisor), 템플릿, .env 로드 |
| `llm` | openai | 에이전트 LLM 호출 (OpenAI) |
| `graph` | neo4j | 2-1 Explorer: 의존 그래프 |
| `report` | markdown, weasyprint | 3 Composer: PDF 저장 |
| `api` | fastapi, uvicorn | 웹 UI 백엔드 API (`src/impact_agent/api`) |
| `dev` | pytest, ruff | 테스트, 린트 |
| `all` | llm + graph + report + api | 전체 런타임 |

필요한 묶음만 설치할 수도 있습니다. 예: `pip install -e ".[llm,graph,dev]"`

### 자주 쓰는 명령

```bash
source .venv/bin/activate
pytest                                    # 테스트
docker compose up -d                      # Neo4j 실행
ruff check src tests                      # 린트
python -m impact_agent "요청 문장" --sm-part product   # 실행
python -m impact_agent --case SC-001 --until retriever  # 정답셋 요청으로 Planner→Explorer→Retriever 까지 (설계: docs/YJ_agent_design.md)
python -m impact_agent --case L-1 --answer-key eval/ground_truth/L-1.json   # + KPI 평가 (PoC)
python scripts/eval_kpi.py <state.json 폴더>   # 저장된 *.state.json 배치 KPI 평가 (예: E2E 실행 결과 폴더)
```

## 웹 UI 실행 (Neo4j → 백엔드 → 프론트)

`frontend/` (React 18 + TypeScript + Vite) + 백엔드 API `src/impact_agent/api/` (FastAPI).
화면 설계서: [docs/ui_design.md](docs/ui_design.md), API 설계서: [docs/api_design.md](docs/api_design.md), Agent 설계서: [docs/YJ_agent_design.md](docs/YJ_agent_design.md)

| 모드 | 필요한 것 | 용도 |
|---|---|---|
| mock (기본) | 프론트만 | 화면 확인. API 는 MSW 목업 (`frontend/src/mocks`) |
| 실제 API | Neo4j + 백엔드 + 프론트, `.env` (LLM 키·Neo4j 계정), `data/` | 실제 분석·적재 현황 |

### 0. 사전 준비 (최초 1회)

| 항목 | 확인 |
|---|---|
| Python 3.11 이상 | `python3 --version` |
| Node.js 18 이상 · npm | `node --version` |
| Docker Desktop | 실행 중이어야 함 |
| `.env` | `cp .env.example .env` 후 `OPENAI_API_KEY`, `LLM_MODEL`, `SOURCE_ROOT_*` 설정. `.env.example` 에 없는 `NEO4J_URI`·`NEO4J_USER`·`NEO4J_PASSWORD` (아래 [Neo4j 실행](#neo4j-실행-docker)), Azure 사용 시 `AZURE_OPENAI_ENDPOINT`·`OPENAI_API_VERSION` 추가 |
| `data/` | 목업 소스·그래프 (git 미포함, 관리자에게 zip 요청) → 저장소 루트에 `data/source`, `data/graph` |

**Python 환경**: 아래 둘 중 하나로 실행한다. conda `base` 의 `python3` 에는 uvicorn·neo4j 가 없어 `No module named uvicorn` 이 난다.

| 방법 | 설정 |
|---|---|
| 1. Python 경로를 직접 지정 | 패키지가 설치된 Python 경로를 `PY` 로 둔다 (`.venv` 를 쓰면 `PY=.venv/bin/python`) |
| 2. conda 환경 사용 | `conda activate <환경>` 후 (처음 한 번) `pip install -r requirements.txt`, 그리고 `PY=python` |

```bash
# 1. 경로 직접 지정
PY=<패키지가 설치된 python3 경로>

# 2. conda 환경
conda activate <환경>
pip install -r requirements.txt            # 처음 한 번 (fastapi · uvicorn · neo4j · openai 포함)
PY=python

$PY -c "import fastapi, uvicorn, neo4j, openai; print('ok')"   # ok 가 나오면 준비 완료
```

아래 명령은 모두 **저장소 루트**에서 실행하고, `$PY` 는 위에서 정한 Python 이다 (터미널을 새로 열면 다시 정한다).

**프론트 패키지**

```bash
cd frontend
npm install
```

### 1. Neo4j 띄우기 + 그래프 적재 (터미널 1)

```bash
docker compose up -d                       # 이미 떠 있으면 그대로
docker ps --filter name=neo4j              # STATUS 가 Up 이면 OK
docker compose logs -f neo4j               # 처음 띄울 때 "Started." 확인 후 Ctrl+C

$PY scripts/build_graph.py --load          # data/source → data/graph 생성 + Neo4j 재적재 (기존 :Node 삭제 후 다시)
```

- 적재할 때 영역별 적재 기록(`:LoadMeta`: 시각·소스 파일별 해시·노드/관계 수)이 남는다 → 적재 현황 화면의 master 반영률 · 적재 이력. 소스(`data/source`)를 바꿨으면 다시 적재한다.
- 확인: http://localhost:7474 에서 `neo4j` / `.env` 의 `NEO4J_PASSWORD` 로 연결 후 입력줄에 쿼리를 넣고 `Cmd + Enter` (쿼리 전에는 빈 화면이 정상).
  ```cypher
  MATCH (n:Node) RETURN n.domain, count(*)                       // tos 322 · swing 366
  MATCH (m:LoadMeta) RETURN m.domain, m.loaded_at, m.nodes, m.rels
  ```

### 2. 백엔드 띄우기 (터미널 2)

```bash
PYTHONPATH=src PYTHONWARNINGS=ignore $PY -m uvicorn impact_agent.api.app:app --app-dir src --port 8000
```

- `Uvicorn running on http://127.0.0.1:8000` 이 나오면 준비 완료. `.env` 는 앱이 직접 읽는다.
- 터미널에는 에이전트 진행만 찍힌다 (▶ 시작 · ■ 끝 한 줄 · ↓ 라우팅). HTTP 요청 줄이 섞이는 게 싫으면 실행 명령에 `--no-access-log` 를 붙인다:
  ```
  ▶ 2-1 Explorer · 왕복 1/3 · 19:02:47
  ■ 2-1 Explorer     완료 14.5s │ 시작 노드 4 · 경로 10 │ tool 누적 7/20 │ 토큰 13,134
    ↓ COMPOSER  단순 흐름 (Simulator · Verifier 생략)
  ```
- 상세(에이전트마다 **받은 입력 → 동작 → 근거 → 결과**)는 `outputs/logs/<세션 8자>.log` 와 세션 화면 **실행 로그**·진행 패널의 단계별 펼치기에 남는다. **터미널에도 전체를 찍으려면** `.env` 에 `AGENT_LOG=full` (또는 실행 명령 앞에 `AGENT_LOG=full`, 시연용). 동작 섹션에는 LLM 판정·ReAct 와 함께 SE 단계의 **LLM 판정 → 규칙 검증·보정**(Simulator 영역 판정 보정·경로 제외 가드, Verifier 점검·대안 판단·재계획 판단, Composer 출력 검증·체크리스트 출처·권장 판단)이 남는다:
  ```
  ▶ 2-1 Explorer · 왕복 1/3 · 19:02:47
    ▸ 받은 입력 ← Planner
      항목 1     screen_change → 목표 EntryHandler · forward · hop 5 | …
    ▸ 동작 · 규칙 탐색
        Screen   '결합상품' (+동의어 2) → 채택 SCR:ZCMBSS0100010 '결합상품 가입' 1.05 / 탈락 SCR:ZPRDSS0200010 0.4, …
      tool 5     탐색 SCR:ZCMBSS0100010 외 1 forward → EntryHandler → 경로 10 · 노드 12 · 23ms
    ▸ 결과 → Retriever
      경로 1     [1] target_reached: SCR:ZCMBSS0100010 > TP:DocRsvSvc.regDocRsv
    ■ 완료 14.5s │ 시작 노드 4 · 경로 10 │ tool 누적 7/20 │ 토큰 13,134
  ```
- 확인 (다른 터미널):
  ```bash
  curl -s -H "X-User: %EA%B9%80%EA%B4%80%EB%A6%AC" localhost:8000/api/sessions    # []  (값은 "김관리" URL 인코딩, 없으면 401)
  curl -s -H "X-User: %EA%B9%80%EA%B4%80%EB%A6%AC" "localhost:8000/api/graph/status?sm=dbm" | python3 -m json.tool | head -20   # connected: true, nodes 322
  ```
- 코드를 고치면 `Ctrl + C` 후 다시 실행 (또는 `--reload` 추가). **재시작하면 세션은 사라진다** (메모리 저장). KPI 기록은 `outputs/kpi/records.json` 에 남는다.

### 3. 프론트 띄우기 (터미널 3)

```bash
cd frontend
VITE_USE_MOCK=false npm run dev            # 실제 API 모드 (Vite 프록시가 /api 를 :8000 으로)
```

→ http://localhost:5173

- 매번 변수를 붙이기 싫으면 `echo "VITE_USE_MOCK=false" > .env.local` (git 미포함) 후 `npm run dev`. 지우면 다시 mock.
- mock 모드: `npm run dev` 만 실행 (백엔드·Neo4j 불필요).

### 4. 화면에서 시나리오 실행

사이드바 아래 mock 사용자를 **김관리**(상품·DBM)로 둔다. 분석은 한 번에 하나씩 돌므로 앞 분석이 끝난 뒤 다음을 넣는다.
요청 문장이 정답셋 `eval/ground_truth/<요건ID>.json` 의 `request_text` 와 같으면 KPI 품질 지표(핵심 [변경] 노드 정밀도·[변경] 재현율·노드 탐지율, 보조, 판정 품질)까지 계산된다.

| 시나리오 | SM | 흐름 | 소요 |
|---|---|---|---|
| tos `SC-003` | DBM | 복잡 → 레포트·체크리스트 | 약 3분 |
| swing `L-4` | 상품 | 단순 응답 | 약 1분 |

단순 응답에 "상세 분석 권장" 이 뜨면 **상세 분석 요청** 을 눌러 같은 세션을 복잡 요건으로 다시 분석할 수 있다 (재분석 흐름).

요청 문장 꺼내기:
```bash
$PY -c "import json; print(json.load(open('eval/ground_truth/SC-003.json'))['request_text'])"
```

1. 홈 → SM 토글·입력 유형(요건서) 선택 → 문장 붙여넣기 → 전송(↑) (`⌘ + Enter`)
2. `/session/:id` 오른쪽 진행 패널: 단계·경과 시간·한도 미터(도구 n/20 · 왕복 n/3 · 재계획 n/2)·근거 목록
3. 복잡 결과: 답변 카드의 **레포트·체크리스트** → `/session/:id/report`
4. `/kpi`: 세션별 KPI 기록. 반복 테스트 전에는 **기록 초기화**
5. `/graph`: Neo4j 연결 · master 반영률 · 품질 점검 · 노드 검색(예: `TOSCP0210M`) → 2 hop 미리보기

**적재 현황(`/graph`) 노드 검색 테스트**

화면 위 "적재 기준 SM 파트"를 고른 뒤 2 hop 미리보기의 검색창에 입력한다. node_id·이름·설명 부분 일치이며 접두어(`SCR:`, `TBL:` 등)는 빼도 된다 (최대 12건). 결과를 누르면 그 노드 기준 2 hop 으로 다시 그리고 노드 상세가 열린다.

| SM | 입력 | 기대 결과 |
|---|---|---|
| DBM | (처음 화면) | 기본 노드 `DBIO:DCP_MSGSCRT_S02` 기준 2 hop |
| DBM | `TOSCP0210M` | `SCR:TOSCP0210M` (캠페인 화면, Entry) |
| DBM | `TCH_MMS_SND_TGT` / `TCH_EXT_SND_TGT` | SC-006 자사·타사 발송 대상 테이블 (Table) |
| DBM | `TCP_MSG_SCRT` | 테이블. "컬럼 노드 표시" 체크 시 컬럼까지 |
| DBM | `TBCH1020` | 배치. 프로그램 소스 미제공이라 연결 없음 (품질 점검 "미제공 소스 영역"과 같은 노드) |
| 상품 | (처음 화면) | 기본 노드 `TP:CombScrbSvc.joinComb` 기준 2 hop |
| 상품 | `joinComb` | `TP:CombScrbSvc.joinComb` (결합 가입 서비스, EntryHandler) |
| 상품 | `TB_COMB_GRP` / `TB_COMB_MBR` | 결합 그룹·구성원 테이블 |
| 상품 | `ZCMBSS0100010` | 결합 화면 (Entry) |
| 상품 | `ZDOCBB0100010` | 구비서류 배치 (Entry) |
| 공통 | `MMS`, `발송` 등 단어 일부 | 이름·설명에 포함된 노드 목록 |
| 공통 | 다른 SM 노드 (예: DBM 에서 `joinComb`) | 결과 없음 (SM 별 영역만 검색) |

### 5. 종료

| 대상 | 방법 |
|---|---|
| 프론트 · 백엔드 | 각 터미널에서 `Ctrl + C` |
| Neo4j | `docker compose down` (데이터 유지) |

### 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `No module named uvicorn` | conda base 의 `python3` 로 실행함. 위 "Python 환경" 의 1·2 중 하나로 실행 |
| 화면이 mock 데이터 그대로 | `VITE_USE_MOCK=false` 없이 실행함. 프론트 재시작 |
| 적재 현황 "연결 끊김" · 검색 503 | Neo4j 꺼짐 → `docker compose up -d` |
| master 반영률 0% · 적재 이력 없음 | 적재 기록(`:LoadMeta`) 없는 적재 → `$PY scripts/build_graph.py --load` |
| API 401 | 사용자(`X-User`)가 없거나 미등록. 사용자 목록은 `outputs/admin/users.json` |
| API 403 | 권한 또는 SM 파트 위반. 관리 화면·KPI 초기화는 김관리(관리자)로 전환 |
| 후속 입력 409 | 분석 중. 끝난 뒤 다시 |
| 백엔드 재시작 후 세션이 없음 | 세션은 메모리 저장 (PoC) |

### API 테스트 (LLM · Neo4j 없음)

```bash
PYTHONPATH=src $PY -m pytest tests/test_api.py tests/test_admin_api.py tests/test_graph_api.py -q
```

## 정답셋 16건 E2E 평가 (터미널)

화면 없이 정답셋 16건(`eval/ground_truth`)을 UI 와 같은 전체 흐름(Planner → … → Composer → KPI)으로 돌린다.

**공통**
- 준비: Neo4j 실행(`docker compose up -d`) + `.env`(LLM 키·Neo4j 계정) + `data/`. 백엔드·프론트는 필요 없다
- 명령은 저장소 루트에서, 패키지가 설치된 Python 으로 실행한다 (`run_all.sh` 는 Python 을 자동으로 찾음)
- 결과는 `outputs/runs/` 아래에 생기고 git 에는 올리지 않는다. 끊겨도 같은 명령이면 끝난 케이스는 건너뛴다
- 실행 이름(`<이름>`)·실험 ID 는 자유롭게 정한다 (예: `main-check`, `before`, `after`)
- 평가 방식은 모든 스크립트가 같다 (아래 "테스트 방식")

**용도별 실행 명령**

| 용도 | 명령 | 결과 |
|---|---|---|
| 16건을 한 번 돌려 전체 상태 확인 (통합 테스트) | `VERBOSE=1 zsh scripts/YJ/run_all.sh <이름>` | `outputs/runs/YJ/<이름>/report.html` |
| 일부 케이스만 빠르게 확인 | `PYTHONPATH=src python3 scripts/YJ/run_scenarios.py SC-003 L-4 --run-id <이름>` | `outputs/runs/YJ/<이름>/` |
| 수정 전·후를 1회씩 비교 | 수정 전 `zsh scripts/YJ/run_all.sh before` → 수정 후 `zsh scripts/YJ/run_all.sh after before` | `outputs/runs/YJ/after/report.html` (before 대비 증감) |
| LLM 응답 편차까지 보고 비교 (N회 평균·범위) | `PYTHONPATH=src python3 scripts/SE/run_experiment.py --e2e --repeat 3 --exp-id <ID> --label "<설명>"`, 비교는 다음 실험에 `--compare <이전 ID>` | `outputs/runs/SE/experiments/<ID>/experiment.html` |
| Simulator·Verifier·Composer·KPI 만 바꿨을 때 상류(Planner·Explorer·Retriever) 고정 비교 | 상류 기록 `PYTHONPATH=src python3 scripts/SE/run_scenarios.py <케이스…> --run-id <상류>` → `PYTHONPATH=src python3 scripts/SE/run_experiment.py --from <상류> --repeat 3 --exp-id <ID>` | `outputs/runs/SE/experiments/<ID>/` |
| 실행 없이 리포트만 다시 | 1회 실행: `PYTHONPATH=src python3 scripts/YJ/run_scenarios.py --report <이름> [--baseline <이전 이름>]` / 반복 실험: `… run_experiment.py --exp-id <ID> [--compare <이전 ID>] --report-only` | 같은 폴더 |

- 1회 실행 결과(`outputs/runs/YJ/<이름>/`): `report.html`(지표 · 케이스별 변화 · 판정 흐름 · 단계별 정답 손실 · 개선 후보), `tos/` · `swing/` 아래 케이스별 `<케이스>.md` · `<케이스>.log` · `<케이스>.state.json`, 영역별 `summary.md`
- 반복 실험 결과(`outputs/runs/SE/experiments/<ID>/`): 회차별 `r1 … rN/`, 지표 평균·범위와 케이스 안정성 `experiment.md` · `experiment.html`
- 한 번 실행으로는 작은 변경 효과(±0.05 수준)가 LLM 응답 편차에 묻힐 수 있다. 결론을 낼 비교는 반복 실험으로 본다
- 처음부터 다시: `run_all.sh` 는 `FRESH=1`, `run_scenarios.py` 는 `--fresh`

**테스트 방식** (모든 스크립트 공통, report 맨 위에도 같은 내용). 정답 복잡도는 "사용자가 상세 분석 요청을 눌렀을지"를 정하는 데만 쓰고 에이전트 입력에는 넣지 않는다.

| 1차 판정 | 정답 | 평가에서 하는 일 | 채점 (`detail_flow`) |
|---|---|---|---|
| complex | - | 끝까지 (Verifier 재계획 라운드도 complex 유지) | - |
| simple | complex | 1차를 끝까지 돌린 뒤 **권장 여부와 관계없이 "상세 분석 요청" 클릭 가정** → reanalysis(권장 사유 줄 + "참고: " 줄, 없으면 "상세 분석 요청") → Planner complex 고정 → 탐색부터 끝까지 | 권장 → 상세 분석 가능 / 참고 신호만 / **신호 없음 (놓침)** |
| simple | simple | 단순 응답으로 끝 (권장이 붙어도 누르지 않음) | **불필요한 권장** / 정상 |

KPI Agent 의 `complexity_match` 는 재분석 후 값이라 1차 오판은 report 의 "1차 복잡도 일치"·판정 흐름에서 본다. 1차 KPI 는 `flow.first_kpi` 에 따로 저장.

## 스키마 변경 절차

1. `schemas/schemas.json` 수정
2. `src/impact_agent/schemas/` 의 해당 모델 수정
3. `pytest tests/test_schema_sync.py` 로 두 정의가 일치하는지 확인
