# KPI Agent

## 역할
PoC 평가용 에이전트. **Composer 이후** 실행하며, 결과는 Composer(사용자 결과물)에 반영하지 않는다.
규칙 기반으로 계산하며 LLM 은 쓰지 않는다.

## 입력
- 정답 경로 목록: `AnswerKey.paths` (`eval/ground_truth/<요건ID>.json` 의 `expected.paths` → `load_answer_key`)
  - 경로마다 `nodes`(A→…→끝), `critical`, `status`(confirmed / pending)
- 실행 로그: `AgentState.agent_runs` (Supervisor 가 기록)
- Agent 제시 경로: Composer `complex_report.path_details` / `simple_answer.impact_paths` (없으면 Simulator 경로). 같은 경로는 한 번만 센다.

## 경로 탐지 판정
정답 경로의 앞에서부터 제시 경로가 **순서대로** 따라간 노드 수 k (중간 노드 추가·끝 이후 연장은 허용)

| 판정 | 조건 |
|---|---|
| exact (정확) | k = 정답 경로 길이 |
| partial (부분) | 2 ≤ k < 길이 |
| initial (초기) | k = 1 (시작 노드만) |
| missed (미탐) | k = 0 |
| pending (평가 보류) | 정답 경로가 미확정 — 모든 계산에서 제외, 건수만 공개 |

`path_results` 에 경로별 판정, 마지막으로 따라간 노드, 도달 단계(A~F = 그래프 0~5단계)를 남긴다.

## 핵심 KPI (quality)
| 지표 | 계산 | 기준 |
|---|---|---|
| path_recall (영향 경로 재현율) | exact / 확정 정답 경로 | ≥ KPI_MIN_RECALL |
| critical_miss_rate (Critical 미탐율) | (Critical 확정 경로 − exact) / Critical 확정 경로 — 부분·초기도 미탐 | 권장 0 (측정만) |
| path_precision (경로 정밀도, 가안) | 정확 제시 경로 / (정확 + 부분·초기 + 오탐) | ≥ KPI_MIN_PRECISION |
| path_precision_excl_partial (보조) | 정확 제시 경로 / (정확 + 오탐) | - |
| partial_paths / initial_paths / pending_paths | 판정별 건수 | - |

제시 경로 판정 우선순위: 확정 정답과 정확 일치 → 미확정 정답과 관련(제외) → 확정 정답의 부분·초기 → 오탐(`false_positive_paths`).

## 성능 지표 (performance, 실행 로그)
| 지표 | 계산 | 기준 |
|---|---|---|
| latency_ms (시간) | 에이전트별 실행 시간 합 | - |
| tool_calls | tool 호출 합 | ≤ MAX_TOOL_CALLS |
| explore_loops | Explorer⇄Retriever 반복 횟수 | - |
| replans | 재계획 횟수 | - |
| total_tokens | 입력과 출력 토큰 합 (기록된 경우만) | - |

## 출력
`KpiOutput`. `Supervisor.last_state.kpi` 로 조회한다. 노드 단위 보조 집계(matched / missed / extra nodes)도 함께 남긴다.
