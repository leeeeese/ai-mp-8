# KPI Agent 설계서 (v0.2 초안)

> 자연어 요건 기반 영향도 분석 AI Agent PoC의 성능 측정용 평가 Agent 설계안.
> 작성일: 2026-10-26 / 상태: 초안 (미결 항목은 11장 참고)

## 변경 이력

| 버전 | 변경 내용 |
|---|---|
| v0.1 | 최초 초안. 노드 목록 기반 탐지율·미탐률·정밀도 정의 |
| v0.2 | 최신 Agent Workflow와 Neo4j 노드 정의 반영 |

v0.2 주요 변경:

- KPI Agent 위치를 **Composer 이후의 평가 분기(점선)** 로 변경하고, 입력을 "정답 + 실행 로그"로 구체화했다.
- Agent 출력은 산출물(레포트·체크리스트) 문장이 아니라 **Supervisor가 기록하는 실행 로그의 노드 목록**에서 읽는다.
- 그래프 노드 유형을 시스템별로 확장했다(tos: Controller·Service·SQL 추가, swing: TP·Module).
- 평가 범위 지정을 `eval_max_stage`에서 `eval_target_labels`(노드 유형 목록)로 바꿨다. DBIO처럼 단계가 맥락에 따라 달라지는 노드 때문이다.
- 집계 축에 SM 파트, 노드 유형, 단순/복잡 flow를 추가했다.
- Planner 판정 정확도 등 확장 KPI 후보를 정리했다(미확정).

---

## 1. 목적과 범위

- **목적:** 영향도 분석 Agent의 최종 출력(영향받는 노드 목록)을 정답셋과 대조해 탐지율·미탐률 등 KPI를 자동 산출한다.
- **용도:** PoC 검증과 고도화(개선 전후 비교)를 위한 **평가 모드**다. 실제 운영 흐름에서는 사용하지 않는다.
- **범위 안:** 노드 단위 탐지/미탐/오탐 판정, 노드 유형·단계·중요도·시스템·SM 파트별 집계, 측정 리포트 생성.
- **범위 밖:** 노드 간 연결(엣지) 정확도, E 코드·F 결과값 수준의 상세 검증, 레포트·체크리스트·회신 메일의 문장 품질.
- **원칙:** 핵심 KPI 판정은 **LLM 없이 결정론적 로직**으로 수행한다. 같은 입력이면 항상 같은 결과가 나와야 개선 전후 비교가 의미를 가진다.

## 2. 워크플로상의 위치

```
요건서 + SM 파트 선택
        │
        ▼
   Supervisor ◀──────────── (재계획) ──────────┐
   (순서·재계획 제어, 실행 로그 기록)            │
        │                                       │
        ▼                                       │
   1. Planner (복잡도 · 목표 역할 · 방향 · hop 상한)
        │  판정 결과로 이후 flow 결정            │
        ▼                                       │
   2-1 Explorer ⇄ 2-2 Retriever                 │
        │                 │                     │
        │ 단순            │ 복잡                │
        │                 ▼                     │
        │          2-3 Simulator ─▶ 2-4 Verifier ┘
        │                              │
        ▼                              ▼
              3. Composer
                   ┆ (평가, PoC)
                   ▼
              KPI Agent ◀┄┄ 정답 노드 목록 + 실행 로그
```

- KPI Agent는 Composer에서 점선으로 이어진 **PoC 평가 분기**다. 평가 모드가 꺼져 있으면 호출하지 않는다.
- 입력은 **정답 노드 목록**(사전 작성)과 **실행 로그**(Supervisor 기록)다.
- Composer 산출물(복잡 요건 결과물, 단순 요건 응답)은 평가 입력으로 쓰지 않는다. 최종 노드 목록을 문장에서 파싱하면 오류가 섞이기 때문이다.
- 실행 로그 기반의 사후 평가라서 워크플로 실행과 분리해 **배치로 돌릴 수 있다**(워크플로 지연 없음).
- 최종 노드 목록의 추출 위치는 flow별로 다르다.
  - 단순 요건: Retriever → Composer로 가므로 Retriever의 최종 결과
  - 복잡 요건: Simulator → Verifier를 거친 결과 (재계획이 있었으면 마지막 라운드)
- 단순/복잡은 검증 단계(Verifier)의 유무가 달라 결과 특성이 다르므로 **flow별로 나눠 집계**한다.

## 3. 평가 기준 (그래프 모델)

영향도 그래프는 Neo4j 하나에 tos와 swing을 같은 라벨 체계로 저장한다(다이어그램의 영역 구분은 설명용).

### 3.1 노드 유형

| 단계 | 시스템 | 노드 유형 | 식별 키 | 비고 |
|---|---|---|---|---|
| 0 진입 | 공통 | Screen | 화면 ID | 주 노드 |
| 1 처리 진입 | tos | Controller | 메서드 | |
| 1 처리 진입 | tos, swing | Batch | Job ID (tos는 Pro*C .pc) | 주 노드 |
| 1 처리 진입 | swing | TP | 서비스명 | |
| 2 업무 로직 | tos | Service | 메서드 | |
| 2 업무 로직 | tos, swing | DBIO | 직접 호출 | |
| 2 업무 로직 | swing | Module | 공통 모듈 | |
| 3 데이터 접근 | tos | SQL | XML 쿼리 · Pro*C SQL | |
| 3 데이터 접근 | swing | DBIO | Module 경유 | |
| 4 테이블 | 공통 | Table | 테이블 | |
| 5 컬럼 | 공통 | Column | 컬럼 · 코멘트 | |

### 3.2 엣지 유형 (참고)

| 엣지 | 연결 |
|---|---|
| OPENS | Screen → Screen |
| CALLS | Screen → Controller (tos), Screen → TP (swing) |
| INVOKES | Controller → Service, Batch(tos) → DBIO, TP → Module, Module → DBIO, TP·Module·Service 자기 호출 |
| EXECUTES | Service → SQL, Batch(tos) → SQL |
| READS · WRITES | SQL · DBIO → Table |
| REFERENCES | Table → Table |
| HAS_COLUMN | Table → Column |

### 3.3 평가에 미치는 영향

- **정답셋은 경로가 아닌 "영향받는 노드 목록"** 이다. 엣지는 평가하지 않는다.
- **DBIO의 단계는 맥락에 따라 2 또는 3이다.** Batch나 TP가 직접 호출하면 2, Module을 경유하면 3이다. 그래서 단계는 노드 유형의 고정 속성이 아니라 정답셋의 노드별 기록값으로 두고, **기본 집계 축은 노드 유형**으로 한다.
- 노드 ID는 `<노드 유형>:<식별 키>` 형식을 제안한다(예: `Screen:<화면ID>`, `Table:<테이블명>`). 그래프의 실제 키와 맞춰야 한다(11장).

## 4. 입력

### 4.1 정답셋 (사전 작성)

사례당 JSON 한 건. 실행 전에 확정하고 이후 변경하지 않는다(변경은 새 버전으로 관리).

```json
{
  "case_id": "REQ-001",
  "system": "swing",
  "sm_part": "상품",
  "requirement": "자연어 요건 원문",
  "complexity": "단순 | 복잡",
  "eval_target_labels": ["Screen", "TP", "Module", "DBIO", "Table", "Column"],
  "answer_nodes": [
    {"node_id": "Screen:<화면ID>", "type": "Screen", "stage": 0, "severity": "Major"},
    {"node_id": "DBIO:<DBIO명>",   "type": "DBIO",   "stage": 3, "severity": "Critical"},
    {"node_id": "Table:<테이블명>", "type": "Table",  "stage": 4, "severity": "Critical"}
  ],
  "excluded_nodes": [
    {"node_id": "Module:<모듈명>", "reason": "현업 질문 필요"}
  ],
  "expected_planner": {"complexity": "복잡", "target_label": "Table"},
  "basis": {"doc": "분석서 v1.2", "approved": true, "answer_set_version": "v1"}
}
```

- `severity`: Critical(미탐 시 장애·과금 오류 가능) / Major(오동작·운영 이슈 가능) / Minor(참고·부수적).
- `eval_target_labels`: 이 사례에서 평가할 노드 유형. **평가자가 정의**하며 Planner의 실행 시 설정과 분리한다(5.2).
- `excluded_nodes`: 현업 질문 필요·자료 부족으로 정답을 확정하지 못한 노드. 분모에서 빼고 건수를 공개한다.
- `expected_planner`: 확장 KPI(6.3)를 쓸 때만 필요한 선택 필드다.

### 4.2 실행 로그 (Supervisor 기록)

```json
{
  "run_id": "RUN-001",
  "case_id": "REQ-001",
  "agent_version": "v0.3",
  "flow": "복잡",
  "planner": {"complexity": "복잡", "target_label": "Table", "direction": "하향", "hop_limit": 5},
  "replan_count": 1,
  "steps": [
    {"agent": "Planner", "started_at": "...", "finished_at": "..."},
    {"agent": "Explorer", "started_at": "...", "finished_at": "..."}
  ],
  "final_nodes": [{"node_id": "Screen:<화면ID>"}, {"node_id": "Table:<테이블명>"}]
}
```

- `final_nodes`는 사람이 보완하기 **전** 결과이며, 재계획이 있었으면 마지막 라운드 결과다.
- 노드 ID는 그래프의 ID를 그대로 쓴다. 이름 문자열 매칭은 쓰지 않는다.
- `final_nodes`가 없으면 평가 불가로 처리하고 사유를 기록한다.
- 로그 필드는 Supervisor의 로그 기록 방식과 합의해야 한다(11장).

### 4.3 수동 입력 (참고 KPI용, 선택)

사례 단위 측정표 CSV: 수작업 총시간, 사람의 검토·보완 시간, 검토 항목 수(전체·불필요·중복), 유용성 점수(5점 척도 3항목), 평가 환경. Agent 실행 시간은 실행 로그에서 자동 계산한다.

## 5. 판정 규칙

### 5.1 노드 판정

| 판정 | 조건 |
|---|---|
| 탐지 (TP) | 정답 노드가 `final_nodes`에 있음 |
| 미탐 (FN) | 정답 노드가 `final_nodes`에 없음 |
| 오탐 (FP) | 출력 노드가 정답·제외 목록에 없고, 유형이 `eval_target_labels`에 포함됨 |
| 평가 제외 | `excluded_nodes`에 있는 노드 (출력에 있어도 오탐으로 세지 않음) |
| 범위 밖 | 유형이 `eval_target_labels`에 없는 정답·출력 노드 (별도 기록, KPI 분모·분자에서 제외) |

### 5.2 평가 범위

- 평가 범위는 정답셋의 `eval_target_labels`로 정한다. Planner의 목표 역할·hop 상한과는 **분리**한다. Planner가 범위를 좁게 잡아 점수를 올리는 것을 막기 위해서다.
- Planner 설정과 평가 범위의 일치 여부는 확장 KPI(6.3)로 따로 본다.
- 동일 `node_id`는 한 번만 센다(중복 제거).

## 6. KPI 정의

### 6.1 핵심 KPI

| KPI | 계산식 | 비고 |
|---|---|---|
| 노드 탐지율 (= 재현율) | TP ÷ (TP + FN) × 100 | 핵심 KPI |
| 노드 미탐률 | FN ÷ (TP + FN) × 100 | 100 − 탐지율 |
| Critical 미탐률 | Critical 중 FN ÷ Critical 전체 × 100 | 권장 목표: 0건 |
| 노드 정밀도 | TP ÷ (TP + FP) × 100 | 불필요한 검토 부담 |

- 분모가 0이면 "해당 없음"으로 표시한다.
- **집계 축:** 사례 / 시스템(swing, tos) / SM 파트 / flow(단순·복잡) / 노드 유형 / 단계 / Agent 버전.
- 노드 유형별 탐지율은 "어떤 종류의 노드를 잘 놓치는가"를 보여주고, 단계별 탐지율은 "어디까지 따라갔는가"를 보여준다.
- 경로 기반 판정(정상·부분·초기 탐지)은 쓰지 않는다.
- 평가 제외·범위 밖 건수는 항상 함께 공개한다.

### 6.2 참고 KPI·지표

| 지표 | 산출 | 출처 |
|---|---|---|
| 분석·검토시간 절감률 | (수작업 총시간 − Agent 활용 총시간) ÷ 수작업 총시간 × 100 | 수동 입력 + 로그의 Agent 실행 시간 |
| 불필요·중복 제시율 | 불필요·중복 검토 항목 ÷ 전체 검토 항목 × 100 | 수동 입력 |
| 담당자 유용성 평가 | 5점 척도 3항목 평균과 의견 | 수동 입력 |
| Agent 실행 시간 | 단계별(Planner~Composer) 소요 시간 | 실행 로그 |
| 재계획 횟수 | Verifier → Supervisor 재계획 건수 | 실행 로그 |

### 6.3 확장 KPI 후보 (미확정)

| 후보 | 산출 | 필요 데이터 |
|---|---|---|
| Planner 복잡도 판정 정확도 | `planner.complexity`와 `expected_planner.complexity` 일치율 | 정답셋, 로그 |
| 목표 라벨 일치율 | `planner.target_label`과 `expected_planner.target_label` 일치율 | 정답셋, 로그 |
| 난이도 판정 일치율 | Simulator의 Major/Medium/Minor 판정과 정답 난이도 일치율 | 정답셋에 난이도 추가, 로그에 판정 결과 기록 |

## 7. KPI Agent 내부 처리 흐름

```
load_inputs → validate → extract → normalize → match → classify → aggregate → report
```

| 단계 | 역할 | 방식 |
|---|---|---|
| load_inputs | 정답셋, 실행 로그, 측정표 로드 | 코드 |
| validate | 필수 필드·승인 여부 확인, `case_id` 매칭, `final_nodes` 존재 확인 | 코드 (실패 시 사유 기록 후 해당 사례 건너뜀) |
| extract | 로그에서 `final_nodes`, Planner 설정, 단계별 시간 추출 | 코드 |
| normalize | 노드 ID 정규화, 중복 제거 | 코드 |
| match | 정답 노드와 출력 노드를 집합 연산으로 대조 | 코드 |
| classify | TP/FN/FP/제외/범위 밖 분류, 유형·단계·중요도 부여 | 코드 |
| aggregate | 사례·시스템·SM 파트·유형별 KPI 계산 | 코드 |
| report | 리포트·시트 생성 | 코드 + (선택) LLM 요약 |

- LLM은 report 단계의 자연어 요약에만 쓰고, 숫자는 항상 코드 계산값을 그대로 쓴다.
- 오탐·미탐 원인 추정(그래프 상 인접 노드, 엣지 유형 등)은 LLM으로 보조할 수 있으나 KPI 값에는 반영하지 않는다.

## 8. 출력

1. **사례별 노드 판정표** (CSV/JSON): case_id, run_id, node_id, 유형, 단계, 중요도, 판정.
2. **KPI 집계표** (CSV/JSON): 사례·시스템·SM 파트·flow·Agent 버전별 6장 KPI.
3. **리포트** (md): 요약 수치, 미탐 노드(Critical 우선), 오탐 노드, 평가 제외·범위 밖 건수, 유형별 탐지율.
4. **버전 비교표**: 같은 정답셋에 대한 Agent 버전 간 KPI 차이.

### 판정표 예시 (swing 사례, 설명용 가상값)

| 노드 | 유형 | 단계 | 중요도 | 판정 |
|---|---|---|---|---|
| Screen S1 | Screen | 0 | Major | 탐지 |
| TP1 | TP | 1 | Major | 탐지 |
| Module M1 | Module | 2 | Major | 탐지 |
| Module M2 | Module | 2 | Minor | 미탐 |
| DBIO1 (Module 경유) | DBIO | 3 | Critical | 탐지 |
| Table T1 | Table | 4 | Critical | 탐지 |
| Table T2 | Table | 4 | Major | 미탐 |
| Column C1 | Column | 5 | Critical | 미탐 |
| Module M9 | Module | 2 | — | 오탐 |
| Table T9 | Table | 4 | — | 오탐 |

정답 8개, 출력 7개 기준 결과:

- 노드 탐지율 5/8 = 62.5%, 미탐률 37.5%
- Critical 미탐률 1/3 = 33.3% (C1)
- 노드 정밀도 5/7 = 71.4%
- 노드 유형별 탐지율: Screen 1/1, TP 1/1, Module 1/2, DBIO 1/1, Table 1/2, Column 0/1

## 9. 운영 규칙

- 중요도와 정답 인정 기준, `eval_target_labels`는 평가 전에 확정한다.
- 사람 보완 전 결과로만 탐지 성능을 평가한다.
- 평가 제외 건수와 범위 밖 건수는 리포트에 반드시 공개한다.
- 추가 발견 노드(정답셋에 없지만 실제 영향이 있는 노드)는 담당자가 확인한 후 정답셋 새 버전에 반영한다. 반영 전 결과는 소급 수정하지 않는다.
- 리포트에는 정답셋 버전과 Agent 버전을 함께 기록한다.
- 단순/복잡 flow, swing/tos는 합산 수치와 분리 수치를 모두 보고한다.

## 10. 단계별 구현 제안

1. **1단계:** 실행 로그 스키마를 Supervisor 담당과 합의하고, 정답셋 JSON 스키마와 노드 ID 체계를 확정한다. 사례 2~3건으로 수작업 판정과 결과가 일치하는지 확인한다.
2. **2단계:** extract~aggregate 로직을 구현하고 CSV를 출력한다.
3. **3단계:** 평가 모드 토글과 리포트(md) 생성을 연결한다.
4. **4단계:** 사례를 시스템별 5~8건으로 확대하고, 버전 비교표와 참고 KPI를 연동한다.

## 11. 미결 항목

| # | 항목 | 비고 |
|---|---|---|
| 1 | 노드 ID 체계 | 유형별 식별 키와 Neo4j 키의 매핑, tos/swing 간 이름 충돌 가능성 |
| 2 | 실행 로그 스키마 | `final_nodes`, Planner 설정, 단계별 시간, 재계획 기록. Supervisor 로그 담당과 합의 |
| 3 | 워크플로 다이어그램 표기 | KPI Agent 입력이 "정답 경로 목록"으로 되어 있어 "정답 노드 목록"으로 정정 필요 |
| 4 | DBIO 단계 기록 방식 | 정답셋에서 노드별로 2/3을 직접 기록할지, 그래프에서 자동 산출할지 |
| 5 | `eval_target_labels` 지정 규칙 | 요건 유형별 기본값을 둘지, 사례별로 지정할지 |
| 6 | Critical 분류 기준 | 장애·과금 영향 사례 목록 사전 합의 |
| 7 | 정답셋 작성·승인 담당 | swing / tos 각각의 담당과 승인 절차 |
| 8 | 확장 KPI 포함 여부 | Planner 판정 정확도, 난이도 판정 일치율 |
| 9 | 엣지 정확도 측정 여부 | 현재 범위 밖. 필요 시 정답셋에 엣지 추가 |
| 10 | swing Batch → DBIO, TP → DBIO 엣지 라벨 | 다이어그램에 라벨 미표기. 평가에는 영향 없음 |
| 11 | 구현 프레임워크·실행 환경 | 미정 |
