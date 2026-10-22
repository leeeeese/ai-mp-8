# Architecture

## 상수 (`schemas.json#/constants`)

| 이름 | 값 | 의미 |
|---|---|---|
| MAX_EXPLORE_LOOP | 3 | Retriever 의 `reexplore_request` 로 Explorer 를 다시 돌리는 최대 횟수 |
| MAX_HOPS | 3 | Planner 가 정하는 탐색 hop 상한(`max_hops`)의 최대값 (잠정) |
| MAX_REPLAN | 2 | Verifier 가 `needs_supplement` 를 낼 때 Planner 로 되돌리는 최대 횟수 |
| MAX_TOOL_CALLS | 20 | 요청 하나에서 허용하는 tool 호출 총합 |
| MEMORY_WINDOW_N | 5 | 후속 대화에서 유지하는 최근 턴 수 |
| KPI_MIN_RECALL | 0.8 | KPI: 재현율 하한 (잠정) |
| KPI_MIN_PRECISION | 0.5 | KPI: 정밀도 하한 (잠정) |

## 단계별 데이터 흐름

| 단계 | 입력 (AgentState 필드) | 출력 |
|---|---|---|
| Planner | user_input, replan_feedback | PlannerOutput |
| Explorer | planner (structured_request, target_roles, direction, max_hops), retriever.reexplore_request | ExplorerOutput |
| Retriever | explorer (경로 노드) → `read_source` 직접 조회 | RetrieverOutput |
| Simulator | explorer, retriever | SimulationOutput |
| Verifier | simulation | VerifierOutput |
| Composer | 분석 결과 전체 + followups | ComposerOutput |
| KPI Agent (PoC 평가) | answer_key(정답 경로 목록), agent_runs(실행 로그), simulation / retriever | KpiOutput |

## 분기 규칙

- `complexity == simple` → Explorer/Retriever → Composer → `SimpleAnswer`
- `complexity == complex` → Explorer/Retriever → Simulator → Verifier → Composer → `ComplexReport`
- Explorer 는 Planner 의 `direction`(기본 downstream)·`max_hops`(≤ MAX_HOPS) 로 `target_roles` 까지 탐색한다.
- Retriever 는 벡터 검색 없이 노드 소스를 직접 조회(`read_source`)한다.
- KPI Agent 는 Composer 이후 한 번 실행하는 PoC 평가 단계다. 결과는 `Supervisor.last_state.kpi` 에 남고 Composer 결과에는 반영하지 않는다.
  `AnswerKey` 가 없으면 성능 지표(시간 등)만 계산한다.
- `UserInput.output_options` 에 `mail_draft` / `pdf` 가 있으면 Composer 가 `mail_draft` / `report_file_path` 를 채운다.
- `ComplexReport.stage`: `initial` → (후속 답변 반영) `updated` → `final_for_dev`
- Composer 가 `reanalysis_request` 를 반환하면 Supervisor 가 재분석을 시작한다 (TODO)

## Enum 요약

- Area: screen / db / interface / batch
- Feasibility: feasible / hard / review
- Difficulty: Major / Medium / Minor
- Confidence: confirmed / inferred
- Domain: swing / tos, SmPart: product / marketing
- NodeLabel: Screen / Controller / TP / Batch / Service / Module / DBIO / SQL / Table / Column
- EdgeType: CALLS / INVOKES / EXECUTES / READS / WRITES / REFERENCES / HAS_COLUMN
- KpiCategory: quality / performance
- KpiMetricName: evidence_coverage, confirmed_ratio, area_coverage, unverified_segments, verify_passed, latency_ms, tool_calls, explore_loops, replans, total_tokens

## 데이터 소스

- Explorer: Neo4j 영향도 그래프 (시작 노드 → 1~2 hop). 노드 구조는 아래 참고, 목업 데이터는 `data/README.md`
- Retriever: 그래프 노드가 가리키는 소스 원문 직접 조회 (`read_source`, 벡터 DB 미사용)

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
