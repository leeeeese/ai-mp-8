# Planner 복잡도 판정 · 상세 분석 권장 (To-Be 수정 사항)

> 작성: SE / 대상: Planner·Explorer·Supervisor·UI 담당(YJ)과 함께 작업하는 Claude. 작성일 2026-10-29, 개정 2026-11-02.
> 관련 문서: [YJ_agent_design.md](YJ_agent_design.md) (2 Planner, 3 Explorer), [SE_agent_design.md](SE_agent_design.md) (3-1 단순 요건 응답).

## 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-29 | 최초 작성. Planner 복잡도 기준(요건 문장 인용), reanalysis 시 complex 강제, Composer 상세 분석 권장·사유, UI 재분석 요청, 스키마 동기화 |
| 2026-11-02 | 2026-10-29 수정 사항 1~7 이 모두 반영된 것을 확인하고 표에서 삭제 (Planner 프롬프트 "2. 복잡도"·`apply_reanalysis`·`test_planner`·`test_supervisor`, Composer `detail_reasons`·`detail_analysis_reasons`, UI mock). Composer 권장 신호를 강/약으로 나눈 것(3장)에 맞춰 Planner·Explorer·UI 추가 수정 사항(2장 1~5) 작성 |
| 2026-11-02 (2차) | Composer 단순 응답 정리 반영에 맞춰 추가: 2장 6 (Explorer 단순 요건 경로 수), 2장 5 (UI 표시) 보강, 3장 신호 출처 갱신. Composer 변경 내용은 아래 "SE 반영 내역" |
| 2026-11-02 (3차) | Simulator 출력 정리 반영에 맞춰 추가: 2장 7 (Explorer 경로 순서 = Simulator 판정 범위), 2장 8 (Planner 복잡도 판정 흔들림). SE 반영 내역에 Simulator·Verifier·Composer 프롬프트 추가 |

## 1. 결정 사항

1. **Planner 는 요건 문장에서 확인되는 것만 판정한다.** 그래프·소스를 보지 않으므로, 탐색해야 알 수 있는 사실(공통 사용 여부·영향 범위·구현 난이도)로 복잡도를 정하지 않는다.
2. **simple 로 처리된 요건은 결과에서 "상세 분석 권장"을 근거와 함께 보여 준다.** (2026-11-02 개정) 탐색 신호를 강/약으로 나눠, **강한 신호가 있을 때만** Composer 가 권장 표시와 사유(`detail_analysis_reasons`)를 낸다. 약한 신호는 권장하지 않고 참고 신호(`detail_analysis_notes`)로 보여 준다.
3. **사용자가 확인하면 재분석한다.** UI 의 재분석 요청은 `FollowupInput(type=reanalysis)` 로 Planner 에 들어가고, Planner 는 이 경우 complex 로 판정한다 → Simulator·Verifier 를 거친 복잡 요건 결과물이 나온다.
   (2026-11-02 추가) 후속 입력 내용에는 **권장 사유와 참고 신호를 모두** 넣는다. 권장 여부는 좁게, 재분석 때 확인 범위는 넓게 잡는다.
4. 어느 에이전트도 다른 에이전트가 정한 값을 덮어쓰지 않는다. 복잡도는 Planner 만 정한다 (재분석도 Planner 가 다시 판정하는 것).
5. 공용 데이터(`data/`, `eval/ground_truth`)는 바꾸지 않는다.

```
요건 → Planner(simple) → Explorer ⇄ Retriever → Composer: 단순 응답 + [상세 분석 권장: 사유(강)] + [참고 신호(약)]
                                                           │ 사용자 확인 (UI, 권장일 때)
                                                           ▼
          FollowupInput(type=reanalysis, content=사유 + "참고: " 참고 신호) → Planner(complex) → … → Simulator → Verifier → Composer
```

## 2. 추가 수정 사항 (2026-11-02)

Composer 의 권장 판단이 Planner·Explorer 출력에 더 직접 기대게 되었다 (3장 표의 "출처" 열). 그 입력이 흔들리면 권장이 과하거나 빠진다.

| # | 위치 | 내용 | 이유 (run 20261102-all 근거) | 담당 |
|---|---|---|---|---|
| 1 | `prompts/planner.md` "## 3. 요건 항목 분리" | 항목은 **바뀌는 위치의 종류가 다를 때만** 나눈다고 명시한다 (같은 위치 종류의 변경을 문장마다 쪼개지 않음, 2장 "대상 개수가 아니라 위치의 종류" 기준과 같게). 구조 변경 유형(`data_add`·`column_spec_change`·`query_field_add`·`interface_field_add`)은 **저장·조회·연계 항목 자체가 바뀔 때만** 고른다. 같은 절의 change_type 표 머리글 행이 두 번 들어간 것도 정리 | 강한 신호 "요건 항목 2개 이상이 2개 영역 이상 변경"은 항목 수를, 신규 후보·그래프 밖 신호의 강/약은 구조 변경 유형 여부를 그대로 쓴다. 항목을 더 쪼개거나 유형을 넓게 고르면 권장이 늘어난다 | YJ |
| 2 | `prompts/planner.md` "## 5. 그래프 밖 영역" | `out_of_graph` 에는 **소스 밖 시스템·채널만** 적고, 업무 절차·정책·담당자 안내는 넣지 않는다 (요건서에 없는 내용이면 `open_items`). 판정 예시에 반례 한 줄 추가 | SC-006(운영 문의)에서 "운영 조직의 IT 요청서 접수 정책", "타사 측 문의 접수 및 응대 절차"가 그래프 밖으로 들어가 Composer 신호가 됨. 구조 변경 요건이면 강한 신호라 불필요한 권장으로 이어진다 | YJ |
| 3 | `prompts/planner.md` "## 7. 사용자 상세 분석 요청" | 후속 입력이 "권장 사유" 줄과 "참고: " 로 시작하는 참고 신호 줄로 온다고 적는다 (줄바꿈 구분). 권장 사유는 항목·언급 표현에 반영하고, 참고 신호는 언급 표현(동의어 포함)·확인 대상으로 반영하되 항목을 새로 만드는 것은 필요할 때만. 사유 예시 문구를 현행 형식으로 바꾼다 (예: "변경 대상 TP:… 을 여러 진입점에서 호출 (진입점 2개)", "요건 항목 2개가 screen·db 영역을 함께 변경 (…)", "참고: 공통 모듈 MOD:… 은 탐색에서 제외됨 - …") | 문장 형식이 바뀌었고 (`… 이 경로에 있어` → `변경 대상 …`), 약한 신호도 재분석 입력에 들어간다 (1장 3) | YJ |
| 4 | `agents/explorer.py` · `schemas/explorer.py` `unresolved` | 미해결 항목을 종류가 있는 구조로도 넘긴다 (예: `unresolved_items: [{item_id, kind: new_candidate \| out_of_graph \| unmatched, text}]`). 기존 `unresolved` 문자열 목록은 호환을 위해 유지 | Composer 가 지금은 문자열 안의 "신규 후보"·"그래프 밖" 을 찾아 신호를 가른다 (`explorer.py` 153·209행 문구). 문구가 바뀌면 신호가 조용히 사라진다. 반영되면 Composer 가 `kind` 로 바꾼다 (SE) | YJ (Composer 전환은 SE) |
| 5 | UI 단순 응답 카드 (`AnswerCard`·`types.ts`·`api/views.py`·mock) | `detail_analysis_notes` 를 "참고 신호"로 표시 (권장이 없어도, "권장 판단 제한" 문구 포함). "상세 분석 요청" 버튼은 `content = 사유 + "참고: " + 참고 신호` 로 보낸다 (**한 줄에 하나씩 줄바꿈으로 구분**: Simulator·Verifier 가 "참고:" 로 시작하는 줄을 참고 신호로 읽는다). (2026-11-02 2차) 영향 경로는 `[변경]` 노드를 표시하고 앞 5개만 펼친 뒤 "외 참조 경로 n개" 로 접고, 근거 표도 10행 뒤는 접는다 (이미 `[변경]` 먼저 정렬되어 옴). mock 의 `SC006_DETAIL_REASONS` 등 사유 문구·경로 hop 문구(`[변경]`/`[참조] 경로상 호출·조회 …`)를 현행 형식으로 바꾼다 | 약한 신호는 지금 MD/PDF 에만 보이고 화면에는 안 보인다. 단순 요건도 경로 29~56개·근거 42~59행이 그대로 나온다. 재분석 입력 형식을 평가 스크립트(`scripts/SE/run_scenarios.py`)와 맞춘다 | SE (Composer·Simulator 작업 뒤 UI 일괄 반영) |
| 6 | `agents/explorer.py` 경로 정리 · `rules.py` `CHANGE_TYPE_RULES` (hop 상한) | **단순 요건의 경로 수를 줄이는 방법 검토**: 항목당 경로 상한, 시작 노드 매칭 점수·목표 도달 순 정렬 후 상위만, 또는 단순 요건 change_type 의 hop 상한 하향. 어느 방법이든 정답 노드(`eval/ground_truth` `impacted`·`reference_only`)가 빠지지 않는지 함께 본다 | 단순 요건(항목 1~2개)인데 경로가 많고 실제 변경 경로는 적다: L-1 경로 56개 중 [변경] 경로 1개 (정답 노드 14개), SC-006 29개 중 0개, SC-005 33개. Composer 는 표시만 접고(2026-11-02) 입력·근거·정밀도(KPI 중앙값 0.106, Retriever 노드 기준)는 그대로라 근본 원인은 탐색량이다 | YJ |
| 7 | `agents/explorer.py` 경로 정리 순서 (YJ_agent_design 3-4) | **경로가 많을 때 앞쪽에 둘 경로의 기준 정리**: 요건 항목·시작 노드 매칭이 강한 경로, 목표 역할에 도달한 경로, 변경 후보(쓰기 엣지·요건 컬럼)를 지나는 경로를 앞으로. 2-6 의 경로 수 줄이기와 함께 보면 된다 | Simulator 는 경로 앞 40개(`simulator.MAX_CONTEXT_PATHS`)만 LLM 에 보여 주고, 그 뒤 경로에만 있는 노드는 판정하지 않는다 ("영향 검토 필요" 로 남음, 2026-11-02 SE 반영). run 20261102-v1 에서 complex 5건이 40개를 넘었다: H-1 57, M-2 56, M-3 56, M-1 47, H-2 42. 정답 경로가 뒤로 밀리면 판정에서 빠진다 | YJ |
| 8 | `agents/planner.py` · `prompts/planner.md` "## 2. 복잡도" | **같은 요건의 복잡도 판정이 실행마다 달라지는 것 확인**: 판정 예시(few-shot)에 조건부 변경(예: "특정 구분일 때만 문구·팝업을 바꿔 달라") 사례를 보강하거나, 사용하는 모델이 지원하면 temperature·seed 고정을 검토 | SC-003(정답 complex, 타사회선일 때만 문구 변경 + 등록 시 안내 팝업)이 run 20261102-all 에서는 1차 simple(→ 상세 분석 권장 → 재분석), run 20261102-v1 에서는 처음부터 complex 였다. 판정이 흔들리면 같은 요건이 실행마다 다른 흐름(단순 응답 / 복잡 결과물)을 탄다 | YJ |

### SE 반영 내역 (2026-11-02, 참고)

YJ 작업과 맞물리는 Composer·KPI 변경. 코드는 반영됨.

| 위치 | 내용 |
|---|---|
| `agents/composer.py` `SimpleDraft` | `hops`(경로×노드마다 한 문장) → `changes`(이 요건으로 고칠 노드만, 노드당 한 번). 나머지 hop 은 코드가 `[참조]` 로 채운다. 근거 문장(`claims`) 10개 이내. SC-005 기준 hop 문장 168개(노드 51개) → `[변경]` 노드 수만큼 |
| `agents/composer.py` 결과 순서 | `impact_paths` 는 `[변경]` 노드를 지나는 경로 먼저, `evidence_table` 은 `[변경]` 노드 근거 → `[변경]` 경로 위 노드 근거 → 나머지 순. 데이터는 모두 남기고 MD 는 경로 5개·근거 10행만 펼친다 |
| `agents/composer.py` 참고 신호 | LLM 응답이 없으면(`[변경]` 표시 없음) `detail_analysis_notes` 에 "권장 판단 제한" 문구를 넣는다 (`NO_TAG_NOTE`) |
| `agents/kpi.py` `change_tag_accuracy` | 단순 flow 도 Composer 경로 표시로 잰다. 기준값 (run 20261102-all): SC-005·SC-006·L-2·L-3·L-4 1.0, L-1 0.615 (배치 프로그램 5개를 `[참조]` 로 표시) |
| `scripts/SE/replay_case.py` 스냅샷 | 1차 simple 이면 Composer 까지 실행해 실제 `[변경]` 표시 기반 사유·참고 신호로 reanalysis |
| `agents/simulator.py` `SimulatorDraft` (3차) | `hops`(경로×노드, SC-001 기준 283개 문장·노드 49개) → `nodes`(노드당 한 번, `{node, change, impact}`). 고치는 노드는 빠짐없이, 확인만 하는 노드는 재분석 요청 노드·근거가 필요한 노드만 쓰고 나머지는 코드가 `[참조] 경로상 호출·조회` 로 채운다. LLM 에 보이지 않은 경로(40개 초과)에만 있는 노드는 "영향 검토 필요" (2장 7) |
| `agents/verifier.py` `reanalysis_coverage` (3차) | 재분석 입력의 "참고:" 줄(참고 신호)에만 나온 노드는 경로에 없어도 경고만 (재계획 사유 아님). 요청 노드에 코드가 채운 기본 `[참조]` 문장만 있으면 "판정 문장 없음" 경고 |
| `prompts/composer.md` `changes` (3차) | run 20261102-v1 에서 Composer 가 `[변경]` 노드를 덜 골라(L-2 화면 2개, L-1 설정 테이블) 단순 flow 태그 정확도가 0.94 → 0.81. "넓게 고르지 않는다" 를 "고치는 노드는 빠짐없이" 로 바꿈 |

## 3. 참고: 현행 Composer 권장 신호 (2026-11-02 반영됨)

`composer.detail_reasons`. 공통 모듈·여러 진입점 신호는 Composer 가 `[변경]` 으로 고른 노드(`SimpleDraft.changes`)에서만 센다
(`[참조]` 노드는 거쳐 가기만 하므로 신호가 아님. Simulator 의 공유 노드 판단과 같은 기준).

| 등급 | 신호 | 출처 | 문장 예 |
|---|---|---|---|
| 강 | `[변경]` 노드가 공통 모듈 | Composer hop `[변경]` + 노드 `is_common` | "공통 모듈 CTL:… 이 변경 대상이라 다른 화면·처리 영향 확인 필요" |
| 강 | `[변경]` 노드를 여러 진입점이 지남 | 위 + `explorer.paths` (서로 다른 Entry 2개 이상) | "변경 대상 TP:… 을 여러 진입점에서 호출 (진입점 2개)" |
| 강 | 요건 항목 2개 이상이 2개 영역 이상을 변경 | **`planner.items` 수** + `[변경]` 노드의 영역 (`rules.node_areas`) | "요건 항목 2개가 screen·db 영역을 함께 변경 (…)" |
| 강 / 약 | 매칭 안 된 신규 후보 | `explorer.unresolved` "신규 후보". **구조 변경 유형**이 있으면 강, 없으면 약 | "그래프에 없는 항목이 있어 신규 개발 여부 확인 필요: …" |
| 강 / 약 | 그래프 밖 연계 | `explorer.unresolved` "그래프 밖" (← `planner.out_of_graph`). 구조 변경 유형이 있으면 강, 없으면 약 | "적재 범위 밖 연계 확인 필요: …" |
| 약 | 구조 변경 유형 | `planner.items[].change_type` ∈ data_add · column_spec_change · query_field_add · interface_field_add | "[항목 1] 저장·조회·연계 항목이 바뀌는 요건 (…)" |
| 약 | 탐색에서 제외된 공통 모듈 | `explorer.excluded_common` | "공통 모듈 MOD:… 은 탐색에서 제외됨 - 이 요건으로 바뀌는지 확인" |
| 약 | LLM 판단만 있음 | `SimpleDraft.detail_analysis_recommended` (규칙상 강한 신호 없음) | "원문에서 변경 요소가 보이나 탐색 신호가 없어 상세 분석은 권장하지 않음 (Agent 판단)" |

강한 신호 → `detail_analysis_recommended=true` + `detail_analysis_reasons`. 약한 신호 → `detail_analysis_notes` (권장 안 함).
배경: 이전 기준("놓치지 않는 쪽으로 넓게", 신호 하나면 권장)에서 run 20261102-all 의 simple 6건 모두 불필요한 권장이 붙었다
(경로 위 `[참조]` 노드의 진입점·공통 모듈, 유형만으로 권장, LLM 판단만으로 권장).

## 4. 테스트 (2장 반영 시)

| 대상 | 확인 |
|---|---|
| Planner (`tests/test_planner.py`) | 2-2: 업무 절차만 있는 운영 문의에서 `out_of_graph` 가 비는지 (가짜 LLM 응답이므로 프롬프트 예시로 확인). 2-3: "참고: " 줄이 있는 reanalysis 후속 입력이 LLM 입력에 그대로 들어가는지. 2-8: 조건부 변경 few-shot 추가 시 예시 형식 확인 (판정 흔들림 자체는 정답셋 반복 실행으로 확인) |
| Explorer (`tests/test_explore.py`) | 2-4: 신규 후보·그래프 밖·매칭 실패가 `kind` 와 함께 나오는지, 기존 `unresolved` 문자열 유지. 2-6: 단순 요건 경로 상한·정렬이 정답 경로를 남기는지. 2-7: 경로 정렬 기준대로 앞쪽에 오는지 |
| 정답셋 실행 | `scripts/SE/run_scenarios.py` 로 simple 6건(SC-005·SC-006·L-1~L-4) "불필요한 권장" 0건, SC-003 "권장 → 상세 분석 가능" 유지. 2-6 반영 전후로 simple 케이스 탐지율·정밀도·Composer 지연(타임라인) 비교, `change_tag_accuracy` 가 기준값(위 SE 반영 내역) 아래로 떨어지지 않는지 |
