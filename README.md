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
├── scripts/export_schema.py  # Pydantic 모델 → JSON Schema 재생성 (비교용)
└── docs/architecture.md
```

## 시작하기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
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
