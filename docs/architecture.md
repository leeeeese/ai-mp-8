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
| KPI_MIN_RECALL | 0.8 | KPI: 재현율 하한 (잠정) |
| KPI_MIN_PRECISION | 0.5 | KPI: 정밀도 하한 (잠정) |

## 단계별 데이터 흐름

| 단계 | 입력 (AgentState 필드) | 출력 |
|---|---|---|
| Planner | user_input, replan_feedback | PlannerOutput |
| Explorer | planner.items (탐색 명세), retriever.reexplore_request | ExplorerOutput |
| Retriever | explorer.paths | RetrieverOutput (evidence[].content 원문) |
| Simulator | explorer, retriever | SimulationOutput |
| Verifier | simulation | VerifierOutput |
| Composer | 분석 결과 전체 + followups | ComposerOutput |
| KPI Agent (PoC 평가) | answer_key(정답 노드 목록), agent_runs(실행 로그), simulation(복잡) / retriever(단순) 의 final_nodes | KpiOutput |

## 분기 규칙

Supervisor 는 LangGraph `StateGraph(AgentState)` 의 조건부 엣지로 구현한다 ([YJ_agent_design.md](YJ_agent_design.md) §0).


- `complexity == simple` → Explorer/Retriever → Composer → `SimpleAnswer`
- `complexity == complex` → Explorer/Retriever → Simulator → Verifier → Composer → `ComplexReport`
- KPI Agent 는 Composer 이후 한 번 실행하는 PoC 평가 단계다. 결과는 `Supervisor.last_state.kpi` 에 남고 Composer 결과에는 반영하지 않는다.
  `AnswerKey`(`eval/ground_truth/<요건ID>.json`) 가 없으면 성능 지표(시간 등)만 계산한다. 노드 단위 판정 (설계: [SE_kpi-agent-design.md](SE_kpi-agent-design.md)).
- `UserInput.output_options` 에 `mail_draft` / `pdf` 가 있으면 Composer 가 `mail_draft` / `report_file_path` 를 채운다.
- `ComplexReport.stage`: `initial` → (후속 답변 반영) `updated` → `final_for_dev`
- Composer 가 `reanalysis_request` 를 반환하면 Supervisor 가 재분석을 시작한다 (TODO)

## Enum 요약

- Area: screen / db / interface / batch
- Feasibility: feasible / hard / review
- Difficulty: Major / Medium / Minor
- Confidence: confirmed / inferred
- Domain: swing / tos, SmPart: product / marketing
- NodeRole: Entry / EntryHandler / Logic / DataAccess / Table / Column (영역별 노드 라벨은 `GraphNode.label` 문자열)
- Direction: forward / reverse / forward_then_reverse
- KpiCategory: quality / performance
- KpiMetricName: node_recall, node_miss_rate, critical_miss_rate, node_precision, excluded_nodes, out_of_scope_nodes, latency_ms, tool_calls, explore_loops, replans, total_tokens

## 데이터 소스

- Explorer: Neo4j 영향도 그래프 (목표 역할까지 탐색, full-text 인덱스로 시작 노드 매칭)
- Retriever: 원본 소스·인터페이스 정의서 직접 조회 (벡터 DB 없음)

Planner·Explorer·Retriever·Supervisor 상세는 [YJ_agent_design.md](YJ_agent_design.md).

## 영향도 그래프 노드 구조

| 단계 | tos | swing | 엣지 |
|---|---|---|---|
| 0 진입 | Screen | Screen | Screen -CALLS→ Controller / TP |
| 1 처리 진입 | Controller, Batch | TP (자기 호출), Batch | Controller -INVOKES→ Service, TP -INVOKES→ Module / DBIO, Batch -INVOKES→ DBIO |
| 2 업무 로직 | Service (자기 호출), DBIO | Module (자기 호출), DBIO | Service / Batch -EXECUTES→ SQL, Module -INVOKES→ DBIO |
| 3 데이터 접근 | SQL | DBIO (Module 경유) | SQL / DBIO -READS·WRITES→ Table |
| 4 테이블 | Table | Table | Table -REFERENCES→ Table |
| 5 컬럼 | Column | Column | Table -HAS_COLUMN→ Column |

tos / swing 은 같은 라벨 체계의 하나의 그래프로 저장하고 노드 `domain` 속성으로 구분한다.
탐색 목표로 쓰는 공통 역할 라벨(`NodeRole`)과 영역별 라벨의 매핑은 [YJ_agent_design.md](YJ_agent_design.md) 참고.
