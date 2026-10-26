# Architecture

## 상수 (`schemas.json#/constants`)

| 이름 | 값 | 의미 |
|---|---|---|
| MAX_EXPLORE_LOOP | 3 | Retriever 의 `reexplore_request` 로 Explorer 를 다시 돌리는 최대 횟수 |
| MAX_REPLAN | 2 | Verifier 가 `needs_supplement` 를 낼 때 Planner 로 되돌리는 최대 횟수 |
| MAX_TOOL_CALLS | 20 | 요청 하나에서 허용하는 tool 호출 총합 |
| MAX_HOPS_FORWARD | 5 | 단방향 탐색 hop 상한 (역할 단계 기준) |
| MAX_HOPS_BIDIRECTIONAL | 10 | forward_then_reverse 탐색 hop 상한 |
| MEMORY_WINDOW_N | 5 | 후속 대화에서 유지하는 최근 턴 수 |
| KPI_MIN_EVIDENCE_COVERAGE | 0.8 | KPI: 근거 확보율 하한 (잠정) |
| KPI_MIN_CONFIRMED_RATIO | 0.5 | KPI: confirmed 판정 비율 하한 (잠정) |

## 단계별 데이터 흐름

| 단계 | 입력 (AgentState 필드) | 출력 |
|---|---|---|
| Planner | user_input, replan_feedback | PlannerOutput |
| Explorer | planner.items (탐색 명세), retriever.reexplore_request | ExplorerOutput |
| Retriever | explorer.paths | RetrieverOutput (evidence[].content 원문) |
| Simulator | explorer, retriever | SimulationOutput |
| Verifier | simulation | VerifierOutput |
| KPI Agent | explorer, retriever, simulation, verifier, agent_runs | KpiOutput |
| Composer | 전체 + kpi + followups | ComposerOutput |

## 분기 규칙

Supervisor 는 LangGraph `StateGraph(AgentState)` 의 조건부 엣지로 구현한다 ([YJ_agent_design.md](YJ_agent_design.md) §0).


- `complexity == simple` → Explorer/Retriever → KPI Agent → Composer → `SimpleAnswer`
- `complexity == complex` → Simulator → Verifier → KPI Agent → Composer → `ComplexReport`
- KPI Agent 는 재계획 루프가 끝난 뒤 한 번만 실행한다. 미달 지표는 `warnings` 로 Composer 에 넘긴다.
- `UserInput.output_options` 에 `mail_draft` / `pdf` 가 있으면 Composer 가 `mail_draft` / `report_file_path` 를 채운다.
- `ComplexReport.stage`: `initial` → (후속 답변 반영) `updated` → `final_for_dev`
- Composer 가 `reanalysis_request` 를 반환하면 Supervisor 가 재분석을 시작한다 (TODO)

## Enum 요약

- Area: screen / db / interface / batch
- Feasibility: feasible / hard / review
- Difficulty: Major / Medium / Minor
- Confidence: confirmed / inferred
- Domain: swing / tos, SmPart: product / marketing
- KpiCategory: quality / performance
- KpiMetricName: evidence_coverage, confirmed_ratio, area_coverage, unverified_segments, verify_passed, latency_ms, tool_calls, explore_loops, replans, total_tokens

## 데이터 소스

- Explorer: Neo4j 영향도 그래프 (목표 역할까지 탐색, full-text 인덱스로 시작 노드 매칭)
- Retriever: 원본 소스·인터페이스 정의서 직접 조회 (벡터 DB 없음)

Planner·Explorer·Retriever·Supervisor 상세는 [YJ_agent_design.md](YJ_agent_design.md).
