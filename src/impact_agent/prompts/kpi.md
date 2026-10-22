# KPI Agent

## 역할
PoC 평가용 에이전트. **Composer 이후** 실행하며, 결과는 Composer(사용자 결과물)에 반영하지 않는다.
규칙 기반으로 계산하며 LLM 은 쓰지 않는다.

## 입력
- 정답 경로 목록: `AnswerKey` (`data/ground_truth/<요건ID>.json` 에서 `load_answer_key` 로 생성)
- 실행 로그: `AgentState.agent_runs` (Supervisor 가 기록)
- 분석 결과: 에이전트가 영향으로 낸 노드 (복잡: Simulator 경로 노드 / 단순: Retriever 근거 노드)

## 품질 지표 (quality, AnswerKey 가 있을 때)
| 지표 | 계산 | 기준 |
|---|---|---|
| recall (재현율) | 찾은 정답 / 정답(expected_nodes) | ≥ KPI_MIN_RECALL |
| miss_rate (미탐율) | 1 − recall | - |
| precision (정밀도) | 정답·허용 노드 / 낸 노드 | ≥ KPI_MIN_PRECISION |

## 성능 지표 (performance, 실행 로그)
| 지표 | 계산 | 기준 |
|---|---|---|
| latency_ms (시간) | 에이전트별 실행 시간 합 | - |
| tool_calls | tool 호출 합 | ≤ MAX_TOOL_CALLS |
| explore_loops | Explorer⇄Retriever 반복 횟수 | - |
| replans | 재계획 횟수 | - |
| total_tokens | 입력과 출력 토큰 합 (기록된 경우만) | - |

## 출력
`KpiOutput` (matched / missed / extra 노드 목록 포함). `Supervisor.last_state.kpi` 로 조회한다.
