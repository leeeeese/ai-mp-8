# Impact Analysis Agent

요건(ITSD/메일 등)이나 운영 문의를 받아 **화면 · DB · 인터페이스 · 배치** 영역의 영향도를 분석하는 멀티 에이전트입니다.
모든 에이전트의 입출력은 [`schemas/schemas.json`](schemas/schemas.json)(JSON Schema 2020-12)을 기준으로 정의합니다.

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
└── docs/architecture.md
```

## 시작하기

### 요구 사항
- Python 3.11 이상
- (PDF 저장 시) WeasyPrint 시스템 라이브러리
  - macOS: `brew install pango`
  - Ubuntu/Debian: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`
  - Windows: [WeasyPrint 설치 안내](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows) 참고
- (Explorer) Neo4j 5.x 이상. 로컬에서는 Docker로 띄울 수 있습니다:
  `docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/<비밀번호> neo4j:5`

### 한 번에 설치

```bash
git clone -b dev-by-claude-code https://github.com/leeeeese/ai-mp-8.git
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

### 패키지 묶음 (`pyproject.toml`)

| 묶음 | 패키지 | 용도 |
|---|---|---|
| (기본) | pydantic, jinja2, python-dotenv | 스키마, 흐름 제어, 템플릿, .env 로드 |
| `llm` | anthropic | 에이전트 LLM 호출 (Claude) |
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
ruff check src tests                      # 린트
python -m impact_agent "요청 문장" --sm-part product   # 실행 (에이전트 구현 후)
```

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
