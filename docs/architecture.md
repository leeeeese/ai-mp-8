# Architecture

## 상수 (`schemas.json#/constants`)

| 이름 | 값 | 의미 |
|---|---|---|
| MAX_EXPLORE_LOOP | 3 | Retriever 의 `reexplore_request` 로 Explorer 를 다시 돌리는 최대 횟수 |
| MAX_REPLAN | 2 | Verifier 가 `needs_supplement` 를 낼 때 Planner 로 되돌리는 최대 횟수 |
| MAX_TOOL_CALLS | 20 | 요청 하나에서 허용하는 tool 호출 총합 |
| MEMORY_WINDOW_N | 5 | 후속 대화에서 유지하는 최근 턴 수 |

## 단계별 데이터 흐름

| 단계 | 입력 (AgentState 필드) | 출력 |
|---|---|---|
| Planner | user_input, replan_feedback | PlannerOutput |
| Explorer | planner.structured_request | ExplorerOutput |
| Retriever | explorer.nodes | RetrieverOutput |
| Simulator | explorer, retriever | SimulationOutput |
| Verifier | simulation | VerifierOutput |
| Composer | 전체 + followups | ComposerOutput |

## 분기 규칙

- `complexity == simple` → Explorer/Retriever 다음 바로 Composer → `SimpleAnswer`
- `complexity == complex` → Simulator → Verifier → Composer → `ComplexReport`
- `ComplexReport.stage`: `initial` → (후속 답변 반영) `updated` → `final_for_dev`
- Composer 가 `reanalysis_request` 를 반환하면 Supervisor 가 재분석을 시작한다 (TODO)

## Enum 요약

- Area: screen / db / interface / batch
- Feasibility: feasible / hard / review
- Difficulty: Major / Medium / Minor
- Confidence: confirmed / inferred
- Domain: swing / tos, SmPart: product / marketing
