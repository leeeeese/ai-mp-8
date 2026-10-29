# Planner 복잡도 판정 · 상세 분석 권장 (To-Be 수정 사항)

> 작성: SE / 대상: Planner·Supervisor·UI 담당(YJ)과 함께 작업하는 Claude. 작성일 2026-10-29.
> 관련 문서: [YJ_agent_design.md](YJ_agent_design.md) (2 Planner), [SE_agent_design.md](SE_agent_design.md) (3-1 단순 요건 응답).

## 1. 결정 사항

1. **Planner 는 요건 문장에서 확인되는 것만 판정한다.** 그래프·소스를 보지 않으므로, 탐색해야 알 수 있는 사실(공통 사용 여부·영향 범위·구현 난이도)로 복잡도를 정하지 않는다.
2. **simple 로 처리된 요건은 결과에서 "상세 분석 권장"을 근거와 함께 보여 준다.** 탐색 결과에 상세 분석이 필요해 보이는 신호가 있으면 Composer 가 권장 표시와 사유를 낸다.
3. **사용자가 확인하면 재분석한다.** UI 의 재분석 요청은 `FollowupInput(type=reanalysis)` 로 Planner 에 들어가고, Planner 는 이 경우 complex 로 판정한다 → Simulator·Verifier 를 거친 복잡 요건 결과물이 나온다.
4. 어느 에이전트도 다른 에이전트가 정한 값을 덮어쓰지 않는다. 복잡도는 Planner 만 정한다 (재분석도 Planner 가 다시 판정하는 것).
5. 공용 데이터(`data/`, `eval/ground_truth`)는 바꾸지 않는다.

```
요건 → Planner(simple) → Explorer ⇄ Retriever → Composer: 단순 응답 + [상세 분석 권장: 사유]
                                                           │ 사용자 확인 (UI)
                                                           ▼
                                FollowupInput(type=reanalysis) → Planner(complex) → … → Simulator → Verifier → Composer
```

## 2. 수정 사항

| # | 위치 | 내용 | 담당 |
|---|---|---|---|
| 1 | `src/impact_agent/prompts/planner.md` "## 2. 복잡도" | complex 기준에서 탐색해야 알 수 있는 기준("바꿀 값이 저장되거나 공통코드·다른 처리에서도 쓰임")을 뺀다. `complexity_reason` 에는 근거가 된 **요건 문장 표현을 인용**하게 한다. "애매하면 complex"·운영 문의 기준은 유지 | YJ |
| 2 | `src/impact_agent/agents/planner.py` | 마지막 후속 입력이 `reanalysis` 이면 LLM 판정과 관계없이 `complexity=complex`, `plan` 에 simulator·verifier 포함, `complexity_reason` 앞에 "사용자 상세 분석 요청" (코드 규칙) | YJ |
| 3 | UI (단순 요건 응답 화면) | `detail_analysis_recommended=true` 면 권장 표시와 `detail_analysis_reasons` 를 보여 주고, "상세 분석 요청" 버튼 → `FollowupInput(type=reanalysis, content=<사유 요약>)` 전송. **반영됨 (mock)**: `AnswerCard`·`Session`·`types.ts`(`MessageKind` reanalysis, `AgentAnswer.detail_analysis_recommended`·`detail_analysis_reasons` — 백엔드 `SimpleAnswer` 와 같은 이름)·mock 저장소 | SE |
| 4 | `src/impact_agent/schemas/composer.py` `SimpleAnswer` | `detail_analysis_reasons: list[str]` 추가 (권장 사유) | SE — **반영됨** |
| 5 | `src/impact_agent/agents/composer.py` `detail_reasons` (기존 `rule_detail` 대체) | 기존 `change_type` 기준에 탐색 신호 추가 (아래 표). 신호마다 사유 문장을 `detail_analysis_reasons` 에 넣는다 | SE — **반영됨** |
| 6 | `src/impact_agent/templates/simple_answer.md.j2` | 권장 사유 목록 표시 | SE — **반영됨** |
| 7 | 스키마 동기화 | `python scripts/export_schema.py --write`, `tests/test_schema_sync.py`, 프론트 목업(`frontend/src/mocks/fixtures/scenarios.ts`)에 `detail_analysis_reasons` 반영 | SE (목업은 YJ 확인) — **반영됨** |

### 상세 분석 권장 신호 (5번)

권장은 사용자가 거르므로, 놓치지 않는 쪽으로 넓게 잡는다.

| 신호 | 출처 | 사유 문장 예 |
|---|---|---|
| 구조 변경 유형 (기존) | `planner.items[].change_type` ∈ data_add · column_spec_change · query_field_add · interface_field_add | "저장·조회 항목 추가가 포함된 요건" |
| 공통 노드가 경로에 있거나 탐색에서 제외됨 | 경로 노드의 `is_common`, `explorer.excluded_common` | "공통 모듈 CTL:… 이 경로에 있어 다른 화면·처리 영향 확인 필요" |
| 여러 진입점이 같은 노드를 지남 | `explorer.paths` (서로 다른 Entry 2개 이상) | "TP:… 를 화면·배치 2곳에서 호출" |
| 매칭 안 된 신규 후보 | `explorer.unresolved` 의 "신규 후보" | "그래프에 없는 항목(…)이 있어 신규 개발 여부 확인 필요" |
| 그래프 밖 연계 | `explorer.unresolved` 의 "그래프 밖" | "적재 범위 밖 연계(…) 확인 필요" |

## 3. 테스트

| 대상 | 확인 |
|---|---|
| Planner (`tests/test_planner.py` 신규) | `reanalysis` 후속 입력이면 LLM 이 simple 을 내도 complex · plan 에 simulator·verifier · 사유 문구 |
| Supervisor (`tests/test_supervisor.py`) | simple 실행 → `Supervisor.followup(session_id, reanalysis)` → Simulator·Verifier 를 거쳐 복잡 결과물 |
| Composer (`tests/test_composer.py`) | 신호별로 `detail_analysis_recommended=true` 와 사유, 신호가 없으면 false·빈 목록 |

완료 기준: `pytest` 전체 통과, simple 결과에서 재분석 요청 한 번으로 complex 결과물이 나올 것.
