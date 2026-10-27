# Agent 설계서: KPI Agent (v0.3 초안)

> 담당: **KPI Agent** · Simulator · Verifier · Composer (SE, [SE_agent_design.md](SE_agent_design.md)) / Supervisor · Planner · Explorer · Retriever ([YJ_agent_design.md](YJ_agent_design.md))
> 자연어 요건 기반 영향도 분석 AI Agent PoC 의 성능 측정용 평가 에이전트. 기준: [agent_workflow_diagram.png](agent_workflow_diagram.png), 노드 ID 규칙 [mock_data.md](mock_data.md). 기준일 2026-10-27. 남은 과제는 8장.

## 변경 이력

| 버전 | 변경 내용 |
|---|---|
| v0.1 | 최초 초안. 노드 목록 기반 탐지율·미탐률·정밀도 정의 |
| v0.2 | 최신 Agent Workflow·Neo4j 노드 정의 반영. KPI Agent 를 Composer 이후 평가 분기(점선)로, 입력을 "정답 + 실행 로그"로. 평가 범위를 `eval_max_stage` → `eval_target_labels` 로 (DBIO 단계가 맥락에 따라 달라서). 집계 축에 SM 파트·노드 유형·flow 추가. 확장 KPI 후보 정리 |
| v0.3 | 형식을 YJ 설계서와 통일 (에이전트 단위 역할·입력·처리·출력). 입력·출력을 구현 모델(`AnswerKey`, `AgentRunRecord`, `KpiOutput`)과 그래프 노드 ID(`<접두어>:<키>`)로 표기. 배치 평가(`scripts/eval_kpi.py`) 추가 |

## 0. 한눈에 보기

```
요건서 + SM 파트 선택
  └ Supervisor ──────────────────────────── 실행 로그(AgentRunRecord: 시간·tool·토큰) 기록
      1 Planner → 2-1 Explorer ⇄ 2-2 Retriever ─(단순)─────────────┐
                                     └(복잡)→ 2-3 Simulator → 2-4 Verifier ─(재계획)→ Planner
                                                                  ▼
                                                             3 Composer
                                                                  ┆ (평가, PoC)
                                                                  ▼
      KPI Agent   규칙  정답 노드 목록(AnswerKey) + 실행 로그 → 노드 판정 → KPI 집계
```

| 에이전트 | LLM | 입력 (AgentState) | 출력 | 구현 |
|---|---|---|---|---|
| KPI Agent | - (리포트 요약만 선택) | `answer_key`, `planner`, `retriever`(단순) / `simulation`(복잡), `agent_runs`, `explore_loop_count`, `replan_count` | `KpiOutput` | `agents/kpi.py`, `schemas/kpi.py` |
| 배치 평가 | - | 저장된 `<case>.state.json` + `eval/ground_truth/<case>.json` | `judgements.csv`, `cases.csv`, `summary.json` | `scripts/eval_kpi.py` |

### 설계 원칙

- **목적:** Agent 의 최종 출력(영향받는 노드 목록)을 정답셋과 대조해 탐지율·미탐률 등 KPI 를 자동 산출한다.
- **평가 모드 전용.** PoC 검증과 고도화(개선 전후 비교)용이다. 실제 운영 흐름에서는 쓰지 않으며, 결과를 Composer 에 전달하지 않는다.
- **결정론적 판정.** 핵심 KPI 는 LLM 없이 코드로 계산한다. 같은 입력이면 항상 같은 결과가 나와야 개선 전후 비교가 의미를 가진다. LLM 은 리포트 자연어 요약에만 쓰고, 숫자는 항상 코드 계산값을 그대로 쓴다.
- **노드 목록으로 평가.** 정답셋은 경로가 아닌 "영향받는 노드 목록"이다. 엣지와 경로 기반 판정(정상·부분·초기 탐지)은 쓰지 않는다.
- **산출물 문장은 쓰지 않는다.** Composer 산출물(레포트·체크리스트)에서 노드를 파싱하면 오류가 섞이므로, 단계별 출력의 노드 ID 를 그대로 읽는다. 이름 문자열 매칭은 쓰지 않는다.
- **사후 평가.** 실행 로그 기반이라 워크플로와 분리해 배치로 돌릴 수 있다 (워크플로 지연 없음).

**범위**

| 범위 안 | 범위 밖 |
|---|---|
| 노드 단위 탐지/미탐/오탐 판정, 노드 유형·단계·중요도·시스템·SM 파트·flow 별 집계, 측정 리포트 | 노드 간 연결(엣지) 정확도, E 코드·F 결과값 수준의 상세 검증, 레포트·체크리스트·회신 메일의 문장 품질 |

상수 (`schemas/schemas.json#/constants`): `KPI_MIN_RECALL` 0.8, `KPI_MIN_PRECISION` 0.5 (잠정), `MAX_TOOL_CALLS` 20 (tool 호출 상한 지표).

---

## 1. KPI Agent

**역할**: 정답 노드 목록과 실행 로그를 대조해 노드별 판정(TP/FN/FP/제외/범위 밖)을 내리고, 품질·성능 KPI 를 집계한다. 분석 판단은 하지 않는다 (LLM 미사용).

**입력**

| 필드 | 사용 |
|---|---|
| `answer_key` (`AnswerKey`) | 정답 노드·평가 제외 노드·평가 범위 (1-2) |
| `planner.complexity` | flow 판단 (단순 / 복잡) → `final_nodes` 추출 위치 |
| `retriever.evidence[].node_id`, `retriever.nodes_without_evidence` | 단순 flow 의 `final_nodes` |
| `simulation.area_impacts[].paths[].hops[].node` | 복잡 flow 의 `final_nodes` (`dropped_paths` 제외) |
| `agent_runs[AgentRunRecord]`, `explore_loop_count`, `replan_count` | 성능 지표 |

**처리**

```
load_inputs → validate → extract → normalize → match → classify → aggregate → report
```

| 단계 | 역할 | 방식 |
|---|---|---|
| load_inputs | 정답셋(`load_answer_key`), 실행 로그, 측정표 로드 | 코드 |
| validate | 필수 필드 확인, 사례 ID 매칭, `final_nodes` 존재 확인 | 코드 (실패 시 `evaluable=false` + `skip_reason`, 성능 지표만) |
| extract | 로그에서 `final_nodes`, Planner 설정, 단계별 시간 추출 | 코드 (`KpiAgent.final_nodes`) |
| normalize | 노드 ID 그대로 사용, 중복 제거 | 코드 |
| match | 정답 노드와 출력 노드를 집합 연산으로 대조 | 코드 (`judge`) |
| classify | TP/FN/FP/제외/범위 밖 분류, 유형·단계·중요도 부여 | 코드 |
| aggregate | 사례·시스템·SM 파트·flow·유형·단계별 KPI | 코드 (`quality_metrics`, `group_kpis`, `by_type`, `by_stage`) |
| report | 리포트·시트 생성 | 코드 + (선택) LLM 요약 |

- `final_nodes` 추출 위치는 flow 별로 다르다.
  - 단순 (Retriever → Composer): Retriever 최종 결과 = 근거 노드 + 근거 없음 노드 (재탐색이 있었으면 마지막 라운드)
  - 복잡 (Simulator → Verifier → Composer): Simulator 영향 경로의 노드 (재계획이 있었으면 마지막 라운드)
- 단순/복잡은 검증 단계(Verifier) 유무가 달라 결과 특성이 다르므로 **flow 별로 나눠 집계**한다.
- 오탐·미탐 원인 추정(그래프 상 인접 노드, 엣지 유형 등)은 LLM 으로 보조할 수 있으나 KPI 값에는 반영하지 않는다.

**출력 `KpiOutput`**: `complexity`, `req_id`, `evaluable`, `skip_reason`, `metrics[KpiMetric]`, `agent_runs`, `final_nodes`, `judgements[NodeJudgement]`, `by_type[GroupKpi]`, `by_stage[GroupKpi]`, `missed_nodes`, `false_positive_nodes`, `distractors_found`, `all_passed`, `warnings`

**한도·예외**: `AnswerKey` 가 없으면 성능 지표만 계산 (`evaluable=false`, 경고 없음). `final_nodes` 가 없으면 평가 불가 사유를 `warnings` 에 기록. threshold 가 있는 지표가 미달이면 `all_passed=false` + `warnings`.

### 1-1. 평가 기준 (그래프 모델)

영향도 그래프는 Neo4j 하나에 tos 와 swing 을 같은 라벨 체계로 저장한다 (다이어그램의 영역 구분은 설명용). 노드 유형과 단계는 노드 ID 접두어로 정한다 (`_TYPE_BY_PREFIX`, `_STAGE_BY_PREFIX`).

| 단계 | 노드 유형 | ID 접두어 | tos | swing | 비고 |
|---|---|---|---|---|---|
| 0 진입 | Screen | `SCR:` | ✓ | ✓ | 주 노드 |
| 1 처리 진입 | Controller | `CTL:` | ✓ | | |
| 1 처리 진입 | Batch | `BAT:` | ✓ (Pro*C) | ✓ (Job) | 주 노드 |
| 1 처리 진입 | TP | `TP:` | | ✓ | |
| 2 업무 로직 | Service | `SVC:` | ✓ | | |
| 2 업무 로직 | Module | `MOD:` | | ✓ | 공통 모듈 포함 |
| 2 / 3 | DBIO | `DBIO:` | ✓ (Batch 직접) | ✓ | **직접 호출 2, Module·Service 경유 3** |
| 3 데이터 접근 | SQL | `SQL:` | ✓ (XML · Pro*C) | | |
| 4 테이블 | Table | `TBL:` | ✓ | ✓ | |
| 5 컬럼 | Column | `COL:` | ✓ | ✓ | |

| 엣지 (참고, 평가 안 함) | 연결 |
|---|---|
| OPENS | Screen → Screen |
| CALLS | Screen → Controller (tos), Screen → TP · TP → TP (swing) |
| INVOKES | Controller → Service, Service → Service, Batch(tos) → DBIO / TP → Module·DBIO, Module → Module·DBIO, Batch(swing) → DBIO·Module·TP |
| EXECUTES | Service → SQL, Batch(tos) → SQL, Controller → SQL |
| READS · WRITES | SQL · DBIO → Table |
| REFERENCES | Table → Table (tos) |
| HAS_COLUMN | Table → Column |

- **DBIO 단계는 맥락에 따라 2 또는 3** 이라 노드 유형의 고정 속성이 아니다. 정답 경로에서 직전 노드로 산출하고 (`stage_of`), **기본 집계 축은 노드 유형**으로 한다.

### 1-2. 입력 상세

**정답 노드 목록 `AnswerKey`** (`eval/ground_truth/<요건ID>.json` → `load_answer_key`). 실행 전에 확정하고 이후 바꾸지 않는다 (변경은 새 버전으로).

| 필드 | 내용 | 정답셋 출처 |
|---|---|---|
| `req_id`, `system`, `sm_part`, `complexity` | 집계 축 | `req_id`, `domain`(없으면 노드 접두어로 추정), `sm_part`, `complexity` |
| `eval_target_labels` | 평가할 노드 유형. **평가자가 정의**하며 Planner 실행 설정과 분리 (1-3). 비우면 전체 | `eval_target_labels` |
| `answer_nodes[{node_id, type, stage, severity}]` | 반드시 찾아야 하는 노드 (탐지율 분모) | `expected.impacted` 중 `must_find=true` |
| `excluded_nodes[{node_id, reason}]` | 정답을 확정하지 못한 노드. 분모에서 빼고 건수 공개 | `must_find=false`, `reference_only`, 정답 경로 위 비영향 노드, 미확정(`pending`) 경로 노드 |
| `distractor_nodes` | 키워드가 겹치지만 영향 없는 노드 (오탐 분석용) | `distractors` |

- `severity`: Critical (미탐 시 장애·과금 오류 가능) / Major (오동작·운영 이슈 가능, 기본값) / Minor (참고·부수적).

```json
{"req_id": "H-1", "system": "swing", "sm_part": "product", "complexity": "complex",
 "eval_target_labels": [],
 "answer_nodes": [{"node_id": "SCR:ZCMBSS0100010", "type": "Screen", "stage": 0, "severity": "Major"},
                  {"node_id": "MOD:CombScrbSvc.validateCombMbr", "type": "Module", "stage": 2, "severity": "Critical"}],
 "excluded_nodes": [{"node_id": "MOD:SvcInfoSvc.isWireless", "reason": "reference_only"}]}
```

**실행 로그** (Supervisor 기록, `AgentState`)

| 필드 | 내용 |
|---|---|
| `agent_runs[AgentRunRecord{agent, latency_ms, tool_calls, input_tokens, output_tokens}]` | 에이전트 실행마다 한 줄 |
| `planner` (`complexity`, 항목별 목표 역할·방향·hop 상한) | flow 판단, 확장 KPI |
| `explore_loop_count`, `replan_count` | 성능 지표 |
| 단계별 출력 (`retriever`, `simulation`) | `final_nodes` 추출 |

- `final_nodes` 는 사람이 보완하기 **전** 결과이며, 재계획이 있었으면 마지막 라운드 결과다.
- 노드 ID 는 그래프 ID 를 그대로 쓴다.

**수동 입력** (참고 KPI 용, 선택): 사례 단위 측정표 CSV — 수작업 총시간, 사람의 검토·보완 시간, 검토 항목 수(전체·불필요·중복), 유용성 점수(5점 척도 3항목), 평가 환경. Agent 실행 시간은 실행 로그에서 자동 계산한다.

### 1-3. 판정 규칙

| 판정 (`NodeVerdict`) | 조건 |
|---|---|
| 탐지 `detected` (TP) | 정답 노드가 `final_nodes` 에 있음 |
| 미탐 `missed` (FN) | 정답 노드가 `final_nodes` 에 없음 |
| 오탐 `false_positive` (FP) | 출력 노드가 정답·제외 목록에 없고, 유형이 `eval_target_labels` 에 포함됨 |
| 평가 제외 `excluded` | `excluded_nodes` 에 있는 노드 (출력에 있어도 오탐으로 세지 않음) |
| 범위 밖 `out_of_scope` | 유형이 `eval_target_labels` 에 없는 정답·출력 노드 (별도 기록, KPI 분모·분자에서 제외) |

- 평가 범위는 정답셋의 `eval_target_labels` 로 정하고, Planner 의 목표 역할·hop 상한과 **분리**한다. Planner 가 범위를 좁게 잡아 점수를 올리는 것을 막기 위해서다. 둘의 일치 여부는 확장 KPI(1-4)로 따로 본다.
- 같은 `node_id` 는 한 번만 센다.

### 1-4. KPI 정의

**핵심 KPI (quality)**

| KPI (`KpiMetricName`) | 계산식 | 기준 |
|---|---|---|
| 노드 탐지율 `node_recall` (= 재현율) | TP ÷ (TP + FN) | 핵심 KPI. `KPI_MIN_RECALL` 이상 |
| 노드 미탐률 `node_miss_rate` | FN ÷ (TP + FN) | 1 − 탐지율 |
| Critical 미탐률 `critical_miss_rate` | Critical 중 FN ÷ Critical 전체 | 권장 목표 0건 |
| 노드 정밀도 `node_precision` | TP ÷ (TP + FP) | 불필요한 검토 부담. `KPI_MIN_PRECISION` 이상 |
| 평가 제외 수 `excluded_nodes`, 범위 밖 수 `out_of_scope_nodes` | 건수 | 항상 함께 공개 |

- 비율은 0~1 로 저장하고 리포트에서 % 로 표시한다. 분모가 0 이면 "해당 없음"(지표 생략).
- **집계 축:** 사례 / 시스템(swing, tos) / SM 파트 / flow(단순·복잡) / 노드 유형 / 단계 / Agent 버전.
- 노드 유형별 탐지율은 "어떤 종류의 노드를 잘 놓치는가", 단계별 탐지율은 "어디까지 따라갔는가"를 보여준다.

**참고 KPI·지표 (performance, 수동 입력)**

| 지표 | 산출 | 출처 |
|---|---|---|
| Agent 실행 시간 `latency_ms` | 단계별(Planner~Composer) 소요 시간 합 | 실행 로그 |
| tool 호출 `tool_calls` | 합계 (`MAX_TOOL_CALLS` 이하) | 실행 로그 |
| 재탐색 `explore_loops` · 재계획 `replans` | Explorer ⇄ Retriever 루프 수, Verifier → Supervisor 재계획 수 | 실행 로그 |
| 토큰 `total_tokens` | 입력 + 출력 토큰 합 | 실행 로그 |
| 분석·검토시간 절감률 | (수작업 총시간 − Agent 활용 총시간) ÷ 수작업 총시간 × 100 | 수동 입력 + 로그 |
| 불필요·중복 제시율 | 불필요·중복 검토 항목 ÷ 전체 검토 항목 × 100 | 수동 입력 |
| 담당자 유용성 평가 | 5점 척도 3항목 평균과 의견 | 수동 입력 |

**확장 KPI 후보 (미확정)**

| 후보 | 산출 | 필요 데이터 |
|---|---|---|
| Planner 복잡도 판정 정확도 | `planner.complexity` 와 정답 `complexity` 일치율 | 정답셋, 로그 |
| 목표 역할 일치율 | Planner 항목별 `target_role` 과 정답 기대값 일치율 | 정답셋에 기대값 필요, 로그 |
| 난이도 판정 일치율 | Simulator 요건 전체 난이도(Major/Medium/Minor)와 정답 `difficulty` 일치율 | 정답셋, 로그 ([SE_agent_design.md](SE_agent_design.md) 9장) |

### 1-5. 출력 예시

**사례별 노드 판정표** (swing 사례, 설명용 가상값)

| 노드 | 유형 | 단계 | 중요도 | 판정 |
|---|---|---|---|---|
| `SCR:S1` | Screen | 0 | Major | 탐지 |
| `TP:P1.f1` | TP | 1 | Major | 탐지 |
| `MOD:M1.f1` | Module | 2 | Major | 탐지 |
| `MOD:M2.f1` | Module | 2 | Minor | 미탐 |
| `DBIO:D1.f1` (Module 경유) | DBIO | 3 | Critical | 탐지 |
| `TBL:T1` | Table | 4 | Critical | 탐지 |
| `TBL:T2` | Table | 4 | Major | 미탐 |
| `COL:T1.C1` | Column | 5 | Critical | 미탐 |
| `MOD:M9.f1` | Module | 2 | — | 오탐 |
| `TBL:T9` | Table | 4 | — | 오탐 |

정답 8개, 출력 7개 기준: 노드 탐지율 5/8 = 62.5%, 미탐률 37.5%, Critical 미탐률 1/3 = 33.3% (`COL:T1.C1`), 노드 정밀도 5/7 = 71.4%, 유형별 탐지율 Screen 1/1 · TP 1/1 · Module 1/2 · DBIO 1/1 · Table 1/2 · Column 0/1.

---

## 2. 배치 평가 (`scripts/eval_kpi.py`)

**역할**: 저장된 실행 결과를 정답셋과 대조해 여러 사례의 KPI 를 한 번에 계산한다 (워크플로와 분리된 사후 평가).

**입력**: `<case>.state.json` (폴더 또는 파일 목록, 사례 ID 는 파일명), `eval/ground_truth/<case>.json`, `--agent-version`

**처리**
1. 상태를 읽고 이전 KPI 결과(`kpi`, `answer_key`)는 지우고 다시 계산한다.
2. 사례마다 `KpiAgent` 판정 → 판정 행을 모은다.
3. 축별로 TP/FN/FP 를 **합산한 뒤** 비율을 계산한다 (사례별 비율의 평균이 아님).

**출력** (`--out`, 기본: 입력 폴더의 `kpi/`)

| 파일 | 내용 |
|---|---|
| `judgements.csv` | 사례별 노드 판정표: case_id, agent_version, node_id, 유형, 단계, 중요도, 판정, 출력 여부 |
| `cases.csv` | 사례별 KPI (시스템 · SM 파트 · flow · Agent 버전) |
| `summary.json` | 합산 · 시스템 · SM 파트 · flow · 노드 유형 · 단계별 KPI |
| 리포트 (md, 예정) | 요약 수치, 미탐 노드(Critical 우선), 오탐 노드, 평가 제외·범위 밖 건수, 유형별 탐지율 |
| 버전 비교표 (예정) | 같은 정답셋에 대한 Agent 버전 간 KPI 차이 |

---

## 3. 연결 (Supervisor)

| 연결 | 내용 | 현재 코드 |
|---|---|---|
| Composer → KPI (점선) | Composer 다음 실행. KPI 결과는 Composer 에 전달하지 않는다 | ✅ `COMPOSER → KPI → END` |
| 실행 로그 | 에이전트마다 `AgentRunRecord` 기록 | ✅ Supervisor |
| 정답 로드 | CLI `--answer-key eval/ground_truth/<case>.json` → `state.answer_key` (없으면 성능 지표만) | ✅ |
| 단계 지정 실행 | `analyze(..., until=...)` 이면 Composer·KPI 생략 | ✅ |

---

## 4. 운영 규칙

- 중요도와 정답 인정 기준, `eval_target_labels` 는 평가 전에 확정한다.
- 사람 보완 전 결과로만 탐지 성능을 평가한다.
- 평가 제외 건수와 범위 밖 건수는 리포트에 반드시 공개한다.
- 추가 발견 노드(정답셋에 없지만 실제 영향이 있는 노드)는 담당자가 확인한 후 정답셋 새 버전에 반영한다. 반영 전 결과는 소급 수정하지 않는다.
- 리포트에는 정답셋 버전과 Agent 버전을 함께 기록한다.
- 단순/복잡 flow, swing/tos 는 합산 수치와 분리 수치를 모두 보고한다.

---

## 5. 스키마 요약 (`schemas/kpi.py`)

| 모델 | 내용 |
|---|---|
| enums | `KpiCategory`(quality / performance), `NodeSeverity`(Critical / Major / Minor), `NodeVerdict`(detected / missed / false_positive / excluded / out_of_scope), `KpiMetricName` |
| `AnswerKey` | `req_id`, `system`, `sm_part`, `complexity`, `eval_target_labels`, `answer_nodes[AnswerNode]`, `excluded_nodes[ExcludedNode]`, `distractor_nodes` |
| `AgentRunRecord` | `agent`, `latency_ms`, `tool_calls`, `input_tokens`, `output_tokens` |
| `KpiMetric` | `name`, `category`, `value`, `unit`, `threshold`, `passed` |
| `NodeJudgement` | `node_id`, `type`, `stage`, `severity`, `verdict`, `in_output` |
| `GroupKpi` | `key`, `tp`, `fn`, `fp`, `recall`, `precision` |
| `KpiOutput` | 1장 출력 |

`python scripts/export_schema.py --write` 로 `schemas.json` 갱신, `tests/test_schema_sync.py` 가 일치 검사.

## 6. 실행·검증

```bash
python -m impact_agent --case L-1 --answer-key eval/ground_truth/L-1.json --out L-1.state.json   # Composer → KPI 까지 (state.kpi)
python scripts/eval_kpi.py outputs/runs/YJ/<run_id>                         # 폴더 안 *.state.json 전체
python scripts/eval_kpi.py a/SC-001.state.json b/H-1.state.json --agent-version v0.3 --out outputs/kpi/v0.3
pytest tests/test_kpi.py                                                    # 판정·집계 (LLM·Neo4j 불필요)
```

| 확인 | 내용 |
|---|---|
| 판정 | 탐지·미탐·오탐·평가 제외·범위 밖 각각, 중복 노드 한 번만 |
| 정답 로드 | `must_find`·`reference_only`·pending 경로 → 정답/제외 분류, DBIO 단계 2/3 산출 |
| flow | 단순 = Retriever 노드, 복잡 = Simulator hop 노드, 결과 없음 → `evaluable=false` |
| 지표 | 분모 0 생략, threshold 미달 → `all_passed=false` + `warnings` |

## 7. 구현 단계

1. 실행 로그 스키마를 Supervisor 담당과 합의하고, 정답셋 스키마와 노드 ID 체계를 확정한다. 사례 2~3건으로 수작업 판정과 결과가 일치하는지 확인한다. — ✅
2. extract~aggregate 로직을 구현하고 CSV 를 출력한다. — ✅ (`agents/kpi.py`, `scripts/eval_kpi.py`)
3. 평가 모드 토글과 리포트(md) 생성을 연결한다.
4. 사례를 시스템별 5~8건 이상으로 확대하고 (현재 swing 10 · tos 6), 버전 비교표와 참고 KPI 를 연동한다.

## 8. 남은 과제

1. **노드 ID 체계**: `<접두어>:<키>` 로 고정됨 (2026-10-25, [mock_data.md](mock_data.md)). tos/swing 간 이름 충돌 여부만 계속 확인
2. **실행 로그 스키마**: `AgentRunRecord` 로 반영. Planner 설정·재계획 라운드별 기록 범위는 Supervisor 담당과 계속 합의
3. **워크플로 다이어그램 표기**: KPI Agent 입력이 "정답 경로 목록"으로 되어 있어 "정답 노드 목록"으로 정정 필요 (YJ 설계서 0·5장 표기 포함)
4. **DBIO 단계 기록 방식**: 지금은 정답 경로에서 자동 산출 (`stage_of`), 정답셋에 `stage` 가 있으면 우선. 경로에 없는 DBIO 는 단계 미상(`?`)
5. **`eval_target_labels` 지정 규칙**: 요건 유형별 기본값을 둘지, 사례별로 지정할지 (현재 비우면 전체)
6. **Critical 분류 기준**: 장애·과금 영향 사례 목록 사전 합의
7. **정답셋 작성·승인 담당**: swing / tos 각각의 담당과 승인 절차
8. **확장 KPI 포함 여부**: Planner 판정 정확도, 목표 역할 일치율, 난이도 판정 일치율
9. **엣지 정확도 측정 여부**: 현재 범위 밖. 필요 시 정답셋에 엣지 추가
10. **swing Batch → DBIO, TP → DBIO 엣지 라벨**: 다이어그램에 라벨 미표기 (그래프는 `INVOKES`). 평가에는 영향 없음
11. **구현 프레임워크·실행 환경**: KPI Agent 는 Supervisor(LangGraph) 노드로 구현됨. 배치 평가 실행 환경(주기·저장 위치)은 미정
12. **리포트(md)·버전 비교표**: 미구현
