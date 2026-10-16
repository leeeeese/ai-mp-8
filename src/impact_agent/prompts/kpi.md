# KPI Agent

## 역할
분석 결과의 품질 지표와 실행 성능 지표를 측정한다. 규칙 기반으로 계산하며 LLM 은 쓰지 않는다.
단순 흐름에서는 Retriever 다음, 복잡 흐름에서는 Verifier 다음에 실행하고, 결과를 Composer 에 넘긴다.

## 품질 지표 (quality)
| 지표 | 계산 | 기준 |
|---|---|---|
| evidence_coverage | (탐색 노드 − 근거 없는 노드) / 탐색 노드 | ≥ KPI_MIN_EVIDENCE_COVERAGE |
| confirmed_ratio | confirmed hop / 전체 hop (복잡만) | ≥ KPI_MIN_CONFIRMED_RATIO |
| area_coverage | 판정한 영역 수 / 4 (복잡만) | - |
| unverified_segments | 확인하지 못한 구간 수 (복잡만) | - |
| verify_passed | Verifier 결과 pass = 1 (복잡만) | = 1 |

## 성능 지표 (performance)
| 지표 | 계산 | 기준 |
|---|---|---|
| latency_ms | 에이전트별 실행 시간 합 | - |
| tool_calls | tool 호출 합 | ≤ MAX_TOOL_CALLS |
| explore_loops | Explorer⇄Retriever 반복 횟수 | - |
| replans | 재계획 횟수 | - |
| total_tokens | 입력과 출력 토큰 합 (기록된 경우만) | - |

## 출력
`KpiOutput`. 기준에 못 미친 지표는 `warnings` 에 넣고, Composer 가 이를 분석 한계에 반영한다.
