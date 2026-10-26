# Impact Analysis Agent

요건(ITSD/메일 등)이나 운영 문의를 받아 **화면 · DB · 인터페이스 · 배치** 영역의 영향도를 분석하는 멀티 에이전트입니다.
모든 에이전트의 입출력은 [`schemas/schemas.json`](schemas/schemas.json)(JSON Schema 2026-10) 기준으로 정의합니다.

## 흐름

```
요건서 + SM 파트 선택 (UserInput)
        │
        ▼
   Supervisor ◀───────────── 재계획 (Verifier: needs_supplement, ≤ MAX_REPLAN)
        │                                                         │
        ▼                                                         │
 1. Planner ── 복잡도 판단 · flow 설계                            │
        │                                                         │
        ▼                                                         │
 2-1 Explorer ⇄ 2-2 Retriever   (재탐색 ≤ MAX_EXPLORE_LOOP)        │
   (Neo4j)         (벡터 DB)                                       │
        │ 단순          │ 복잡                                     │
        │               ▼                                          │
        │        2-3 Simulator ──▶ 2-4 Verifier ───────────────────┘
        │                               │ pass
        ▼                               ▼
 3. KPI Agent ── 품질 · 성능 지표 측정
        │
        ▼
 4. Composer ── 레포트 · 체크리스트 / 회신 메일 초안 · PDF 저장 (요청 시)
        │
        ├─▶ 복잡: ComplexReport (가능 여부·난이도, 현업 확인 체크리스트)
        └─▶ 단순: SimpleAnswer (요약 결론, 영향 노드·근거)
```

| 에이전트 | 출력 스키마 | 사용 tool |
|---|---|---|
| Planner | `PlannerOutput` | - |
| Explorer | `ExplorerOutput` | `search_graph_nodes`, `expand_graph`, `get_node_detail` |
| Retriever | `RetrieverOutput` | `search_source` |
| Simulator | `SimulationOutput` | - |
| Verifier | `VerifierOutput` | - |
| KPI Agent | `KpiOutput` | - (규칙 기반, LLM 미사용) |
| Composer | `ComposerOutput` | `save_report` |

## 폴더 구조

```
.
├── schemas/
│   └── schemas.json          # 단일 원천(Single Source of Truth): $defs + constants
├── src/impact_agent/
│   ├── __main__.py           # CLI 진입점
│   ├── config.py             # constants (MAX_EXPLORE_LOOP 등)를 schemas.json에서 로드
│   ├── state.py              # 에이전트 간 공유 상태(AgentState)
│   ├── schemas/              # $defs → Pydantic 모델 (담당 에이전트별 분리)
│   │   ├── enums.py          #   Area, Difficulty, Feasibility, ... (enum 전체)
│   │   ├── inputs.py         #   UserInput, InputMeta, FollowupInput
│   │   ├── planner.py        #   StructuredRequest, OpenItem, PlanStep, PlannerOutput, ReplanFeedback
│   │   ├── explorer.py       #   GraphNode, GraphEdge, StartNodeChoice, ExplorerOutput
│   │   ├── retriever.py      #   Evidence, ReexploreRequest, RetrieverOutput
│   │   ├── simulator.py      #   PathHop, ImpactPath, Alternative, AreaImpact, SimulationOutput
│   │   ├── verifier.py       #   VerifyCheck, VerifierOutput
│   │   ├── kpi.py            #   KpiMetric, AgentRunRecord, KpiOutput
│   │   ├── composer.py       #   SimpleAnswer, ComplexReport, ChecklistItem, MailDraft, ComposerOutput, ...
│   │   └── tools.py          #   tool 입출력 (SearchGraphNodes*, ExpandGraph*, SearchSource*, SaveReport*)
│   ├── agents/               # 에이전트 구현 (supervisor = 흐름 제어, kpi = 지표 측정)
│   ├── tools/                # tool 구현 + TOOL_REGISTRY
│   ├── prompts/              # 에이전트별 시스템 프롬프트 (*.md)
│   └── templates/            # Composer markdown 템플릿 (Jinja2)
├── tests/                    # 스키마 동기화 · Supervisor 루프 · KPI · 템플릿 테스트
├── scripts/
│   ├── setup.sh / setup.ps1  # 가상환경 + 패키지 일괄 설치
│   └── export_schema.py      # Pydantic 모델 → JSON Schema 재생성 (비교용)
├── requirements.txt          # pip install -r requirements.txt (= .[all,dev])
├── docker-compose.yml        # 로컬 Neo4j (2-1 Explorer)
└── docs/architecture.md
```

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
| (기본) | pydantic, jinja2, python-dotenv | 스키마, 흐름 제어, 템플릿, .env 로드 |
| `llm` | openai | 에이전트 LLM 호출 (OpenAI) |
| `graph` | neo4j | 2-1 Explorer: 의존 그래프 |
| `vector` | chromadb | 2-2 Retriever: 벡터 DB 근거 검색 |
| `report` | markdown, weasyprint | 4 Composer: PDF 저장 |
| `dev` | pytest, ruff | 테스트, 린트 |
| `all` | llm + graph + vector + report | 전체 런타임 |

필요한 묶음만 설치할 수도 있습니다. 예: `pip install -e ".[llm,graph,dev]"`

### 자주 쓰는 명령

```bash
source .venv/bin/activate
pytest                                    # 테스트
docker compose up -d                      # Neo4j 실행
ruff check src tests                      # 린트
python -m impact_agent "요청 문장" --sm-part product   # 실행 (Composer 구현 후)
python -m impact_agent --case SC-001 --until retriever  # 정답셋 요청으로 Planner→Explorer→Retriever 까지 (설계: docs/YJ_agent_design.md)
```

## 웹 UI

`frontend/` (React 18 + TypeScript + Vite, API 는 MSW mock). 실행: `cd frontend && npm install && npm run dev` → http://localhost:5173
화면 설계서: [docs/ui_design.md](docs/ui_design.md), Agent 설계서: [docs/YJ_agent_design.md](docs/YJ_agent_design.md)

## 스키마 변경 절차

1. `schemas/schemas.json` 수정
2. `src/impact_agent/schemas/` 의 해당 모델 수정
3. `pytest tests/test_schema_sync.py` 로 두 정의가 일치하는지 확인

## TODO

- [ ] LLM 프로바이더 선택 및 각 에이전트 `run()` 구현 (structured output)
- [ ] 그래프 DB / 소스 검색 백엔드 연결 (`tools/graph.py`, `tools/source.py`)
- [ ] 후속 입력(`FollowupInput`) 및 `ReanalysisRequest` 처리, MEMORY_WINDOW_N 기반 대화 메모리
- [ ] MAX_TOOL_CALLS 강제
- [ ] 회신 메일 초안 / PDF 저장 구현
- [ ] KPI 기준값(KPI_MIN_*) 확정 및 LLM 토큰 사용량 기록
