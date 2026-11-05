# Planner · Explorer 반영 결과와 SE 반영 요청 (To-Be)

> 작성: YJ / 대상: Simulator · Verifier · Composer · KPI Agent 담당(SE)과 함께 작업하는 Claude. 작성일 2026-11-03.
> 이전 판(2026-11-02, SE 작성)의 "2. 추가 수정 사항"(YJ 담당 1~9)을 반영한 결과와, 그 과정에서 나온 SE 반영 요청을 정리한다.
> 관련 문서: [YJ_agent_design.md](YJ_agent_design.md) (2 Planner, 3 Explorer), [SE_agent_design.md](SE_agent_design.md).

## 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-29 | (SE) 최초 작성. Planner 복잡도 기준, reanalysis 시 complex, Composer 상세 분석 권장·사유, UI 재분석 요청 |
| 2026-11-02 | (SE) Composer 권장 신호 강/약에 맞춘 YJ 추가 수정 사항 1~9 |
| 2026-11-03 | (YJ) 이 판으로 덮어씀: 1~9 반영 결과, E2E 16건 테스트(v6-1 · v6-2 · v6-3), SE 반영 요청 |
| 2026-11-03 | (SE) 4장 #2·#3 처리 현황, 6장 YJ 반영 요청 추가 (6-1 Logic 시작 노드 경로의 진입점 역추적, 6-2 무관 경로 줄이기) |

## 1. 결정 사항 (유지)

1. Planner 는 요건 문장에서 확인되는 것만으로 복잡도를 판정한다. 복잡도는 Planner 만 정한다 (재분석도 Planner 가 다시 판정).
2. simple 응답의 상세 분석 권장은 강한 신호만 권장, 약한 신호는 참고 신호(`detail_analysis_notes`).
3. 사용자 재분석 요청은 `FollowupInput(type=reanalysis, content=권장 사유 줄 + "참고: " 참고 신호 줄)` 로 Planner 에 들어가 complex 로 판정된다.
4. 공용 데이터(`data/`)는 바꾸지 않는다.

## 2. YJ 반영 결과 (이전 판 2장 1~9)

| # | 요청 | 반영 | 위치 |
|---|---|---|---|
| 1 | 항목 분리 기준 · 구조 변경 유형 | 바뀌는 위치의 종류나 **고르게 될 change_type** 이 다를 때만 나눔. 구조 변경 유형은 저장·조회·연계 항목 자체가 바뀔 때만. change_type 표 머리글 중복 정리 | `prompts/planner.md` 3장 |
| 2 | 그래프 밖은 소스 밖 시스템·채널만 | 업무 절차·정책·접수 방법은 넣지 않고 `open_items` 로 | `prompts/planner.md` 5장 |
| 3 | 재분석 입력 형식 | "권장 사유" 줄은 항목·언급 표현에, "참고: " 줄은 언급 표현(동의어)·확인 대상에 반영. 사유 예시를 현행 문구로 | `prompts/planner.md` 7장 |
| 4 | 미해결 항목 구조화 | `ExplorerOutput.unresolved_items: [{item_id, kind, text}]` (kind = `new_candidate` · `out_of_graph` · `unmatched` · `tool_limit`). 기존 `unresolved` 문자열 유지 | `schemas/explorer.py`, `schemas.json`, `agents/explorer.py` |
| 6 | 단순 요건 경로 수 | 원인은 ReAct 가 매칭 근거 없이 추가한 출발 노드에서 화면의 모든 호출로 퍼지는 것 → 그런 출발 노드에서 요건 언급 노드를 지나지 않는 경로는 출발 노드당 2개까지 (Retriever 재탐색 요청 노드 제외) | `agents/explorer.py` `_arrange_paths` |
| 7 | 경로 순서 | 언급 노드를 많이 지나는 경로 먼저. 데이터 접근·테이블에서 끝난 경로는 언급 컬럼까지 이어 붙임 | `agents/explorer.py` `_arrange_paths`, `_extend_to_columns` |
| 8 | 복잡도 판정 흔들림 | 조건부 변경 기준(고정 값을 구분별 값으로 → complex) + 경계 사례. Few-shot 은 형식 예시 1 + 경계 사례 5 로 축소 (14,332자 → 8,718자) | `prompts/planner.md` 2장 · 판정 예시 |
| 9 | 공통코드 미탐 (권고) | (b)(c) 반영: 값(Column) 언급 표현이 문구·사유·코드값·설정값이면 공통코드 테이블을 시작 노드로, 그 테이블을 읽는 처리까지 역방향 (공통 제외 안 함). (d) 동의어 보강은 조건부 변경일 때만. (a) 컬럼 comment 검색은 기존 `search_text` 에 이미 포함 | `agents/explorer.py` `_code_table_starts`, `prompts/planner.md` 4장 |

함께 바뀐 것
- 용어사전 코드 삭제: 파일이 없어 쓰이지 않던 코드(`scripts/build_graph.py` · `scripts/graph/model.py` · `tools/graph.py`)와 `MatchSource.GLOSSARY` 제거 → 그래프 결과 동일 (tos 322 · swing 366 노드, 엣지 850). Neo4j 재적재 불필요
- 실행 로그: 에이전트마다 받은 입력 → 동작 → 근거 → 결과 (`api/agent_log.py`, `trace.py`). 파트너 에이전트는 코드를 고치지 않고 state·출력에서 뽑음

## 3. 테스트 (E2E 16건, `scripts/YJ/run_all.sh`)

UI 와 같은 전체 흐름(Planner → … → Composer → KPI). 1차 simple · 정답 complex 는 상세 분석 요청을 눌렀다고 가정(권장 사유 + "참고:" 줄). 기록: 로컬 `outputs/runs/YJ/v6/v6-{1,2,3}/report.html`.

| 지표 | v6-1 (수정 전) | v6-2 | v6-3 |
|---|---|---|---|
| 1차 복잡도 일치 | 15/16 | 16/16 | 16/16 |
| KPI 노드 탐지율 | 0.89 | 0.98 | 0.94 |
| must_find | 51/56 | 56/56 | 53/56 |
| Critical 경로 미탐 | 3/24 | 2/24 | 7/24 |
| KPI 노드 정밀도 | 0.118 | 0.121 | 0.153 |
| [변경] 노드 정밀도 | 0.96 | 0.92 | 1.00 |
| [변경] 노드 재현율 | 0.64 | 0.60 | 0.65 |
| Simulator 전달 경로 | 0.87 | 0.73 | 0.75 |
| 불필요한 상세 분석 권장 | 0/6 | 0/6 | 0/6 |
| 평균 지연 · 토큰 | 156초 · 17.1만 | 132초 · 13.7만 | 144초 · 14.9만 |

- v6-2: SC-003 1차 complex · 탐지율 0.33 → 1.00 (공통코드 경로), SC-006 0.67 → 1.00, 경로 −32%, 재계획 2 → 0
- v6-3: 단순 요건 정밀도 회복 (L-3 0.09 → 0.40, L-4 0.06 → 0.20). 재현율 하락은 주로 실행마다 Planner 가 언급 표현을 빼먹는 편차 (YJ 다음 과제)

## 4. SE 반영 요청

| # | 위치 | 내용 | 근거 (v6 E2E) |
|---|---|---|---|
| 1 | `agents/composer.py` `detail_reasons` | 신규 후보 · 그래프 밖 신호를 `explorer.unresolved` 문자열("신규 후보"·"그래프 밖" 문구 찾기) 대신 `explorer.unresolved_items[].kind` (`new_candidate` · `out_of_graph`) 로 판정 | 이전 판 2-4 의 SE 몫. 문자열 문구가 바뀌면 신호가 조용히 사라지는 문제 |
| 2 | `agents/kpi.py` | 노드 정밀도와 함께 **[변경] 노드 정밀도**(Simulator·Composer 가 [변경] 으로 표시한 노드 중 정답 비율) 기록 | KPI 오탐 중 [참조]·표시 없음 노드 비율 v6-1 97.8% · v6-2 98.9% · v6-3 95.7%. [변경] 정밀도 0.96 · 0.92 · 1.00 |
| 3 | `agents/kpi.py` · KPI 설계서 | 정답 노드가 1개뿐인 단순 요건의 정밀도 해석 기준 | SC-005 정밀도 0.02 · 0.02 · 0.03 (탐지율 1.00, [변경] 정밀도 1.00) |
| 4 | `agents/simulator.py` · `prompts/simulator.md` | 정답 수정 영역을 모두 "참조만"으로 판정하는 케이스 확인 | SC-002 · SC-004 [변경] 재현율 0.00 (세 실행 모두). SC-004 는 정답 경로가 모두 Simulator 입력에 있었음 (전달 1.00). 2026-11-05 KPI v0.7 부터 운영 문의(`input_type=ops_inquiry`)는 [변경] 재현율에서 제외 → 이 둘의 판정은 `change_tag_accuracy` 로 확인 |
| 5 | 〃 | [변경] 재현율 낮은 케이스 | M-2 0.17 · H-1 0.33 · M-4 0.50 (평균 0.64 → 0.60 → 0.65) |
| 6 | 〃 | 공통코드 경로(`CTL:CmmCdController.selectCdList` → `SQL:CmmCdMapper.selectCdList` → `TBL:TCP_CMM_CD`)가 이제 입력에 들어감 → 공통코드 데이터 등록을 TBL [변경](add/modify)으로 판정하는지 확인 | SC-003 [변경] 재현율 0.50 (정답 변경 노드 2개 중 1개) |
| 7 | 스키마 공유 | `ExplorerOutput.unresolved_items` 추가, `MatchSource` 에서 `glossary` 제거 | 머지 후 `tests/test_schema_sync.py` 로 확인 |

SE 처리 현황 (2026-11-03, SE):
- #1 반영: `composer.unresolved_signals` 가 `unresolved_items[].kind`(`new_candidate`·`out_of_graph`)로 가름. 종류가 없는 이전 저장 상태만 문구로
- #2 `change_node_precision` 추가 (측정만), #3 정답 노드 1개면 노드 정밀도 하한 미적용 (기록만) — [SE_kpi-agent-design.md](SE_kpi-agent-design.md) v0.4
- #4·#5·#6 반영 (효과 확인 전): SE v3 에서 정답 변경 노드 44개 중 22개를 [변경] 으로 놓쳤고, 18개는 LLM 이 [참조] 로 판정 (입력 상한 탓 0개), 4개는 경로에 없음(상류). Simulator 입력에 `[요건 대상 노드]`(요건 언급과 매칭된 시작 노드, 판정 필수)를 추가하고, 프롬프트에 참조로 잘못 두기 쉬운 경우(데이터 등록·보정 — SC-002 `TCM_USER_AUTH`·SC-003 `TCP_CMM_CD`·M-2 `TB_PROD_RELN`, 처리 분기·검증 추가 — H-1 `joinComb`·M-2 `termProd`, 화면 전달·표시 — SC-004 화면)를 변경 기준으로 명시 ([SE_agent_design.md](SE_agent_design.md) 1장 입력 구성). 확인은 상류 고정 재실행 `scripts/SE/replay_run.py --from 20261103-v3`
- #7 반영된 상태 (`schemas.json` 에 `UnresolvedItem`, `tests/test_schema_sync.py` 통과)

## 5. 확인 방법

```bash
git merge dev                                              # (dev-yj) / (dev) git pull origin dev
PYTHONPATH=src python3 -m pytest -q                        # 396 passed
zsh scripts/YJ/run_all.sh <실행 폴더> <기준 실행>             # 예: v6/v6-4 v6/v6-3 → outputs/runs/YJ/<실행 폴더>/report.html
```

report.html 5장 "개선 후보" 아래쪽 `[SE]` 항목이 이 문서 4장과 같은 내용이다.

## 6. YJ 반영 요청 (SE 작성, 2026-11-03)

### 6-1. Logic 시작 노드 경로의 진입점 역추적 (`agents/explorer.py`)

**현상**: 시작 노드가 Module(Logic)이고 목표 역할도 Logic 이면 `traverse_graph` 가 바로 `target_reached` 로 끝나, 그 노드 하나(또는 Module 끼리만 이어진) 경로가 된다. 이 노드를 누가 호출하는지(진입점 쪽 엣지)는 Explorer 결과(`paths`·`edges`)에 남지 않는다.

| 실행 | 진입 노드 없는 경로 | 예 |
|---|---|---|
| SE v1 (20261102-v1) H-2 | 4건 | `MOD:ProdScrbSvc.checkScrbAvail` (정답 노드) |
| SE v2 (20261103-v2) H-1 | 4건 | `MOD:ProdScrbSvc.checkAgeLimit` · `checkProdReln` · `checkScrbAvail`, `MOD:CombScrbSvc.validateCombMbr → … → MOD:CommCdSvc.getCdAttr` |
| SE v2 H-2 | 6건 | 위 3개, `MOD:CombDcBatch.calcDcAmt`, `MOD:ProdScrbSvc.checkAgeLimit → MOD:AgeCalcUtil.*` 2건 |

그래프에는 호출 쪽이 있다: `BAT:ZCMBBB0100010 -INVOKES-> MOD:CombDcBatch.calcDcAmt`, `TP:ProdScrbSvc.scrbProd · changeFeeProd -INVOKES-> MOD:ProdScrbSvc.checkScrbAvail -INVOKES-> checkAgeLimit · checkProdReln`. H-1 에서는 `BAT:ZCMBBB0100010` 이 같은 항목의 시작 노드였는데도 `calcDcAmt` 로 가는 엣지가 결과에 없었다.

**영향**
- Simulator 영역 배정: 영역은 경로의 진입 노드로 정하므로(SE_agent_design 1-1) 이 경로는 영역이 없다. 전에는 어느 영역 경로에도 들어가지 않아 결과·레포트·KPI 에서 기록 없이 빠졌다 (v1 H-2 `checkScrbAvail` 미탐의 원인).
- 진입점 기반 신호: 공유 노드(`rules.shared_nodes`, 진입점 2개 이상)와 Composer 진입점 수(`entry_points`)는 Explorer 경로 첫 노드가 Entry 인 것만 센다. 이 노드들의 진입점은 세지 않아 공유 노드 판정·난이도 보정·상세 분석 권장 신호가 약하게 나올 수 있다.

**SE 쪽 임시 대응 (반영됨)**: `simulator.assign_orphan_areas` 가 영역 없는 경로에 (1) Explorer 엣지의 호출 노드 영역 → (2) 그래프 역방향 호출 진입점(Screen·Batch, 4 hop) 영역 → (3) screen 순으로 영역을 정하고 분석 한계에 "영역 추정: …" 으로 남긴다. v2 H-1·H-2 기준 (3) 으로 떨어지는 경로는 0건 (`calcDcAmt` → batch). 영역만 보완하고 경로·진입점 신호는 바꾸지 않는다.

**요청**: 진입점(Entry) 없이 끝난 경로(시작 노드가 Logic·DataAccess 등이고 목표 역할에 바로 닿은 경우)는 진입점까지 역방향으로 이어 경로에 넣는다 (예: `BAT:ZCMBBB0100010 → MOD:CombDcBatch.calcDcAmt`). hop 상한·tool 한도 안에서, 진입점이 여러 개면 진입점별 경로로. 반영되면 Simulator 의 "영역 추정" 분석 한계가 0건이 되고 `assign_orphan_areas` 는 안전장치로만 남는다.

**확인**: SE `scripts/SE/run_scenarios.py` 로 H-1·H-2 실행 → `<case>.md` 분석 한계에 "영역 추정" 이 없는지, `summary.json` 의 KPI 탐지율 유지.

### 6-2. 정답 노드를 지나지 않는 경로 줄이기 (`agents/explorer.py`)

**현상**: KPI 노드 정밀도가 낮은 가장 큰 원인이 결과 경로 수다. SE v3 (20261103-v3) 기준 결과 경로 중 정답 노드를 하나도 지나지 않는 경로(무관 경로)가 많고, KPI 오탐 339개 중 143개(42%)가 이런 경로에만 있다. 경로 수는 Explorer 결과에서 정해지고, Simulator·Composer 는 경로를 그대로 쓴다 (Simulator 경로 제외는 v3 에서 0건, 아래).

| 케이스 | 결과 경로 | 무관 경로 | 무관 경로만의 오탐 / 오탐 |
|---|---|---|---|
| M-1 | 34 | 29 (85%) | 26 / 29 |
| M-4 | 23 | 19 (83%) | 17 / 22 |
| SC-006 | 10 | 8 (80%) | 10 / 12 |
| M-2 | 67 | 50 (75%) | 15 / 26 |
| H-2 | 26 | 18 (69%) | 16 / 21 |
| L-4 | 10 | 6 (60%) | 7 / 11 |
| M-3 | 45 | 18 (40%) | 20 / 34 |
| SC-003 · SC-004 · SC-001 · SC-002 · H-1 | 5 · 21 · 22 · 15 · 44 | 30~40% | 2/2 · 6/27 · 8/26 · 5/14 · 5/32 |
| L-1 · L-2 · L-3 · SC-005 | 52 · 3 · 3 · 27 | 4~33% | 5/41 · 0/1 · 0/3 · 1/38 |

결과 경로 = 복잡은 Simulator 영향 경로, 단순은 Composer 단순 응답 경로 (KPI `final_nodes` 와 같음). 무관 경로를 모두 빼면 16건 합산 노드 정밀도 0.135 → 0.213 (상한). 나머지 오탐은 정답 노드를 지나는 경로 위 곁가지 노드(DBIO·SQL·TP·Service)라 경로 수를 줄여도 남는다.

**SE 쪽 상황 (참고)**: Simulator 는 LLM 제외 제안을 가드로 거르는데, 무관 경로 170개 중 138개(81%)가 "목표 역할 도달 + 마지막 노드 근거 있음" 가드에 걸려 제안이 있어도 남는다. 이 가드는 탐지율(Critical 미탐 0) 보호용이라 SE 에서 먼저 완화하지 않는다. "[변경] 노드가 없는 경로 제외" 를 저장 결과에 적용해 보면 복잡 요건 정밀도 0.147 → 0.247 이지만 탐지율 1.00 → 0.90 (정답 4개 손실: `MOD:CombDcBatch.calcDcAmt`, `SQL:tbch1010.make_snd_tgt#2`, SC-003 `TBL:TCP_CMM_CD` · `SQL:CmmCdMapper.selectCdList`) 이라 Simulator 단에서 줄이는 것은 위험하다.

**요청**: 경로를 만드는 단계에서 요건과 무관한 분기를 줄인다. 예: 요건 언급 노드를 지나지 않는 경로의 상한을 단순 요건(2장 #6, 출발 노드당 2개)뿐 아니라 복잡 요건에도 적용할지, 화면의 모든 호출로 퍼지는 분기(M-1·M-4·M-2)를 언급 노드 기준으로 자를지 검토. 정답 경로 재현율(must_find)은 유지해야 한다.

**확인**: 위 표의 무관 경로 비율과 KPI 노드 정밀도, 탐지율 (같은 16건, `scripts/YJ/run_all.sh` 또는 SE `run_scenarios.py`). SE 구간 효과와 섞이지 않게 비교할 때는 `scripts/SE/replay_run.py --from <실행>` 으로 SE 구간만 다시 돌릴 수 있다.
