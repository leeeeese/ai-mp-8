# KPI Agent

## 역할
PoC 평가용 에이전트. **Composer 이후** 실행하며, 결과는 Composer(사용자 결과물)에 반영하지 않는다.
규칙 기반으로 계산하며 LLM 은 쓰지 않는다. 설계: `docs/SE_kpi-agent-design.md`

## 입력
- 정답 노드 목록: `AnswerKey` (`eval/ground_truth/<요건ID>.json` → `load_answer_key`)
  - `answer_nodes`: `impacted` 중 `must_find=true` (유형은 노드 ID 접두어, 단계는 정답 경로로 산출, `severity` 는 선택 필드·기본 Major)
  - `excluded_nodes` (평가 제외): `must_find=false` · `reference_only` · 정답 경로 위 노드(impacted 아님) · 미확정(pending) 경로 노드
  - `eval_target_labels`: 평가할 노드 유형 (선택, 비우면 전체)
- 실행 로그: `AgentState.agent_runs` (Supervisor 가 기록)
- Agent 출력 노드(`final_nodes`, 중복 제거). Composer 산출물 문장은 쓰지 않는다.
  - 복잡 (Retriever → Simulator → Verifier → Composer): Simulator 영향 경로의 노드 (재계획이 있었으면 마지막 라운드)
  - 단순 (Retriever → Composer): Retriever 최종 결과 = 근거 노드(`evidence[].node_id`) + 근거 없음 노드(`nodes_without_evidence`)
  - 결과가 없으면 평가 불가(`evaluable=false`, `skip_reason`)

## 노드 판정
| 판정 | 조건 |
|---|---|
| detected (탐지, TP) | 정답 노드가 `final_nodes` 에 있음 |
| missed (미탐, FN) | 정답 노드가 `final_nodes` 에 없음 |
| false_positive (오탐, FP) | 출력 노드가 정답·제외 목록에 없고, 유형이 `eval_target_labels` 안 |
| excluded (평가 제외) | `excluded_nodes` (출력돼도 오탐 아님, 분모·분자 제외, 건수 공개) |
| out_of_scope (범위 밖) | 유형이 `eval_target_labels` 에 없는 정답·출력 노드 (건수 공개) |

`judgements` 에 노드별 판정표, `by_type` / `by_stage` 에 노드 유형·단계별 탐지 성능을 남긴다.

## 핵심 KPI (quality)
| 지표 | 계산 | 기준 |
|---|---|---|
| node_recall (노드 탐지율) | TP / (TP + FN) | ≥ KPI_MIN_RECALL |
| node_miss_rate (노드 미탐률) | FN / (TP + FN) | - |
| critical_miss_rate (Critical 미탐률) | Critical 정답 중 FN / Critical 정답 | 권장 0 (측정만) |
| node_precision (노드 정밀도) | TP / (TP + FP) | ≥ KPI_MIN_PRECISION |
| excluded_nodes / out_of_scope_nodes | 판정별 건수 | - |

분모가 0 이면 해당 지표를 생략한다 (해당 없음). 비율은 0~1 로 저장한다.

## 판정 품질 (judgement, 측정만)
정답셋의 복잡도·난이도·가능 여부·영역 영향 유형·변경/참조 노드·오답 경로와 실행 결과의 판정을 비교한다. 기준값(threshold)이 없어 `all_passed` 에 영향을 주지 않는다.
비교값이 없거나 해당 flow 가 아니면 생략한다 (단순: complexity_match · detail_recommended / 복잡: 그 밖). 불일치는 `judgement_notes`, 재분석 여부는 `reanalyzed`.
지표 정의는 `docs/SE_kpi-agent-design.md` 1-4 "판정 품질 KPI".

## 성능 지표 (performance, 실행 로그)
| 지표 | 계산 | 기준 |
|---|---|---|
| latency_ms (시간) | 에이전트별 실행 시간 합 | - |
| tool_calls | tool 호출 합 | ≤ MAX_TOOL_CALLS |
| explore_loops | Explorer⇄Retriever 반복 횟수 | - |
| replans | 재계획 횟수 | - |
| total_tokens | 입력과 출력 토큰 합 (기록된 경우만) | - |

## 출력
`KpiOutput`. `Supervisor.last_state.kpi` 로 조회한다.
여러 사례의 배치 평가·집계(시스템 · SM 파트 · flow · 노드 유형 · 단계)는 `scripts/eval_kpi.py`.
