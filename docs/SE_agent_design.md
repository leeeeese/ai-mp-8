# Agent 설계서: Simulator · Verifier · Composer (v0.4 초안)

> 담당: **Simulator · Verifier · Composer · KPI Agent** (SE) / Supervisor · Planner · Explorer · Retriever ([YJ_agent_design.md](YJ_agent_design.md))
> 기준: [agent_workflow_diagram.png](agent_workflow_diagram.png) (최신 Agent Workflow), [data_structure_neo4j.svg](data_structure_neo4j.svg) (그래프 노드 구조 0~5단계), **2026-10-27 Neo4j 적재 그래프** (swing 새 데이터 형식 반영, 1장). 작성일 2026-10-27. 미결 항목은 11장.

## 변경 이력

| 버전 | 변경 내용 |
|---|---|
| v0.1 | 최초 초안 |
| v0.2 | 난이도 기준 1차 확정(그래프 단계 반영), 근거 검증(`evidence_missing`) 확정, 경로 제외(`dropped_paths`) 가드 규칙, 영역 분류를 역할 라벨(`NodeRole`) 기준으로 변경, 재분석은 기존 후속 입력 흐름 사용, tos·swing 대응 추가 |
| v0.3 | 난이도 기준을 영역 / 요건 전체로 분리, 일정 요인 개수 상한 (실제 LLM 검증 결과 반영) |
| v0.4 | **현재 적재 그래프 기준으로 재작성.** 그래프 현황(1장) 추가, swing 새 형식(PL/SQL 패키지 DBIO, Job 정의 Batch, `OPENS`, 정의서 한글 속성) 반영, 진입 노드가 없는 경로·고립 노드·공통 노드 처리, 엣지 `columns`·노드 속성(`images`·`cron`·`code_values`) 활용, 신규 요소(`new_elements`) 출력 추가, 난이도 기준을 정답셋 16건과 대조해 조정 |

### v0.4 에서 달라진 데이터 (요약)

| 구분 | v0.3 작성 시점 | 현재 (2026-10-27 적재) | 이 설계에 주는 영향 |
|---|---|---|---|
| swing 소스 형식 | 어노테이션(`@node`·`@calls`…) 기반 | 코드 관례 파서 (`.scr`·`.pgm`·PL/SQL `.sql`·`.bat`) + 정의서 md 에서 한글 이름·설명 | 근거 원문 형식이 바뀜. 노드 `desc` 가 비어 있는 경우가 많아 LLM 이 `code` 원문을 주로 봐야 함 (1-3) |
| swing DBIO | SQL 매퍼의 문 | **PL/SQL `PACKAGE BODY` 안 함수/프로시저** | DBIO 하나가 여러 테이블을 읽고 쓸 수 있음. 테이블·컬럼 단위 판단은 엣지 `columns` 로 (2장 처리 1) |
| Screen→Screen | 없음 | **`OPENS`** (swing 3, tos 6). 팝업·출력 화면 | 경로에 Entry 가 둘 이상 나옴. 영역은 그대로 screen |
| swing Batch | `@attach` 로 로직 흡수 | `JOB` 정의가 실행 함수를 흡수, **`LAUNCHER` 기동 Job 은 기동기 SQL 도 Batch→DBIO** | 모든 기동 Job 이 `DBIO:BatJobSql.*` 로 연결됨 → 배치 공통 경로 다수 (1-4) |
| Table→Table `REFERENCES` | swing 에도 있었음 | **tos 만 2건** (swing DDL 에 FK 없음) | swing 컬럼 변경의 참조 테이블 영향은 그래프로 못 찾음 → 분석 한계 |
| 인터페이스 | 속성 `interfaces` (tos 위주) | swing **TP 5·Batch 2**, tos **Batch 4** | swing 화면 경로에도 interface 영역이 생김 (정답 M-2 `reference_only`) |
| 정답셋 | tos 6 · swing 10 | 동일 16건, `check_ground_truth.py` 모두 OK. **`expected.new`(그래프에 없는 신규 요소) 6건** | 신규 요소를 낼 출력 필드가 없음 → `new_elements` 추가 (7장) |

## 0. 한눈에 보기

![Agent Workflow](agent_workflow_diagram.png)

```
… 2-2 Retriever ─(단순)──────────────────────────────────────────────┐
        │(복잡)                                                       ▼
        ▼                                                        3 Composer ──▶ 단순 요건 응답 / 복잡 요건 결과물
   2-3 Simulator  영역별 영향 · 가능성 · 난이도 · 대안 · 신규 요소          ┆ (평가, PoC)
        ▼                                                              ▼
   2-4 Verifier   영역 누락 · 근거 검증 ─ 보완 필요 → 재계획(Supervisor → Planner, ≤ MAX_REPLAN)
        │ 통과 · 재계획 한도 초과                                  KPI Agent ◀┄ 정답 노드 목록 + 실행 로그
        ▼
   3 Composer
```

| 에이전트 | flow | 방식 | 입력 (AgentState) | 출력 | 구현 |
|---|---|---|---|---|---|
| 2-3 Simulator | 복잡 | 규칙(경로 골격) + LLM(영향·판정) | planner, explorer, retriever, replan_feedback | `SimulationOutput` | `agents/simulator.py`, `prompts/simulator.md` |
| 2-4 Verifier | 복잡 | 규칙 점검 + LLM(대안 근거) | planner, explorer, retriever, simulation, replan_feedback | `VerifierOutput` | `agents/verifier.py`, `prompts/verifier.md` |
| 3 Composer | 단순·복잡 | 코드(구조화 항목) + LLM(문장) + Jinja2 | 전체 + followups | `ComposerOutput` | `agents/composer.py`, `prompts/composer.md`, `templates/*.md.j2`, `tools/report.py` |

### 공통 원칙

- **데이터는 바꾸지 않는다.** `data/`(소스·정의서·그래프 seed·Neo4j)와 정답셋(`eval/ground_truth`)은 팀 공통으로 운영하는 기준 데이터다. 데이터의 빈틈(설명 누락, swing `REFERENCES` 없음, 적재 범위 밖 소스 등)은 에이전트 쪽 규칙·분석 한계로 처리하고, 검증용 가상 사례가 필요하면 `tests/` 의 fixture 로 만든다.
- **규칙 먼저, LLM 은 보완.** yj 의 Explorer·Retriever 와 같은 방식이다. 노드·경로·근거 연결처럼 사실 확인이 되는 부분은 코드로 만들고, 영향 설명·판정 사유·결론 문장만 LLM 이 쓴다.
- **LLM 없이도 끝까지 실행된다.** LLM 키가 없거나 호출이 실패하면 규칙 결과만으로 출력을 만든다 (판정은 `review`, 문장은 규칙 문구). pytest 와 KPI 배치 평가가 LLM 없이 돈다. LLM 클라이언트는 `llm.react_llm()` 과 같은 방식으로 얻는다.
- **노드는 그래프 노드 ID 로 쓴다.** `PathHop.node` 는 반드시 `<접두어>:<키>` 그래프 ID 다 (복잡 flow 의 KPI `final_nodes` = Simulator 경로 노드). 그래프에 아직 없는 노드(신규 컬럼·DBIO·배치)는 hop 이 아니라 `new_elements` 로 낸다 (v0.4).
- **영역별 라벨이 아니라 역할로 판단한다.** tos(Controller·Service·SQL·DBIO)·swing(TP·Module·DBIO) 라벨 차이는 `GraphNode.role`(`NodeRole`) 로 흡수한다 (Explorer·Planner 와 같은 기준, 1장).
- **확인된 사실과 추정을 구분한다.** 근거(Evidence)가 있는 hop 만 `confirmed`, 나머지는 `inferred`. 근거 없이 구현 가능 여부를 확정하지 않고 `review` 로 둔다 (`prompts/_common.md`).
- **실행 로그.** LLM 을 쓴 에이전트는 `self.last_usage` 에 토큰 사용량을 남긴다 (Supervisor 가 `AgentRunRecord` 에 기록). tool 을 쓰면 출력의 `tool_call_count` 를 채운다.
- **Structured Output.** LLM 응답은 Pydantic 모델(`*Draft`)로 받고, 최종 출력 모델은 코드가 조립한다 (Planner 의 `PlannerDraft` 방식).

### 영역(Area) 분류 규칙

Simulator·Verifier·Composer 가 같은 함수(`rules.path_areas`)를 쓴다. 판단 기준은 `GraphNode.role` 이다.

| 역할 (`NodeRole`) | tos / swing 라벨 | 영역 |
|---|---|---|
| Entry | Screen (팝업·출력 화면 포함) | screen |
| Entry | Batch | batch |
| EntryHandler, Logic | Controller·Service / TP·Module | 경로 진입 노드(Entry)의 영역 |
| DataAccess, Table, Column | SQL·DBIO / DBIO, Table, Column | db |
| (속성) | 노드 `properties.interfaces` 가 있거나 근거에 `source_kind=interface_spec` 이 있는 노드 | interface (위 영역에 추가) |
| (그래프 밖) | Explorer `unresolved` 의 그래프 밖 채널·전문 (`ExploreItem.out_of_graph`) | interface, 경로 없이 `unverified_segments` 로 |

- 경로 하나가 여러 영역에 걸칠 수 있다 (예: Screen → TP → Module → DBIO → Table 은 screen + db).
- 4개 영역을 항상 모두 출력한다. 경로가 없는 영역은 `impact_type=none` (Verifier 의 영역 누락 점검 기준).
- **현재 그래프에서 확인된 경로 모양** (정답셋 경로 기준, v0.4 명시):

| 경로 모양 | 예 (정답셋) | 영역 |
|---|---|---|
| Screen → Screen(`OPENS`) → Controller → … | SC-004 `SCR:TOSCP0300M → SCR:TOSCP0301P → CTL:…` | screen (+ db) |
| Batch → TP (`INVOKES`, 화면 서비스 재사용) | H-1 `BAT:ZDOCBB0100040 → TP:CombScrbSvc.joinComb` | batch. **같은 TP 가 화면 경로에도 있으면 두 영역 모두** |
| TP → TP (`CALLS`) | H-2 `TP:ProdScrbSvc.getFeeChgInfo → TP:ProdInfoSvc.getSaleProdList` | 진입 영역 |
| Screen 단독 (1노드) | L-2, L-4, SC-005 | screen |
| Batch 단독 (나가는 엣지 없음) | SC-006 참고 `BAT:TBCH1020`·`TBCH4020` (인터페이스만 있음) | batch (+ interface) |
| EntryHandler 에서 시작 (진입 없음) | SC-003 `CTL:CmmCdController.selectCdList → SQL:… → TBL:TCP_CMM_CD` | screen 으로 본다 (기존 규칙) |
| Table 에서 시작 (진입 없음) | SC-002·SC-006 `TBL:… → COL:…` | db 만 |

---

## 1. 현재 그래프 (2026-10-27 적재 기준)

`python scripts/build_graph.py --load` 결과를 Neo4j 에서 직접 조회한 값이다. 이 설계는 이 데이터를 그대로 쓰며 바꾸지 않는다. 공통 데이터가 갱신되면 이 장부터 다시 맞춘다.

### 1-1. 노드 (688)

| 단계 | 역할 (`NodeRole`) | swing (366) | tos (322) |
|---|---|---|---|
| 0 진입 | Entry | Screen 13, Batch 10 | Screen 8, Batch 7 |
| 1 처리 진입 | EntryHandler | TP 34 | Controller 13 |
| 2 업무 로직 | Logic | Module 24 | Service 23 |
| 3 데이터 접근 | DataAccess | DBIO 60 (PL/SQL 패키지 함수) | SQL 42 (MyBatis XML · Pro*C), DBIO 4 (배치 직접 호출) |
| 4 테이블 | Table | 23 | 22 |
| 5 컬럼 | Column | 202 | 203 |

### 1-2. 엣지 (850)

| 엣지 | swing (491) | tos (359) |
|---|---|---|
| CALLS | Screen→TP 40, TP→TP 18 | Screen→Controller 12 |
| OPENS | Screen→Screen 3 | Screen→Screen 6 |
| INVOKES | TP→DBIO 45, TP→Module 30, Batch→DBIO 27, Module→DBIO 23, Module→Module 12, Batch→Module 12, Batch→TP 2 | Controller→Service 13, Service→Service 9, Batch→DBIO 4 |
| EXECUTES | — | Service→SQL 36, Batch→SQL 11, Controller→SQL 1 |
| READS / WRITES | DBIO→Table 49 / 28 | SQL→Table 24 / 25, DBIO→Table 10 / 3 |
| REFERENCES | — (swing DDL 에 FK 없음) | Table→Table 2 (`via=CMPN_ID`) |
| HAS_COLUMN | 202 | 203 |

엣지 속성: `CALLS.event`·`OPENS.event` (화면 이벤트), `READS.columns`·`WRITES.columns` (SQL 이 접근하는 컬럼, 쉼표 구분), `REFERENCES.via`, `EXECUTES.note`·`INVOKES.note`.

### 1-3. 이 설계가 쓰는 노드 속성

| 속성 | 있는 노드 | 사용처 |
|---|---|---|
| `code` | Column 제외 전부 (Batch 는 Job 정의 + 실행 함수 원문) | LLM 판단 원문 (Retriever 근거가 없을 때 보조) |
| `desc`, `program_name`, `program_desc` | swing 은 정의서에서 채움. **채움률이 낮음**: DBIO 15/60, TP 22/34, Module 18/24 | 노드 설명. 비어 있으면 이름 + `code` 앞부분으로 대신한다 |
| `interfaces` | swing TP 5 (`IF_PAY_0010/0020/0030`), swing Batch 2 (`IF_SMS_0010`, `IF_CDTS_0010`), tos Batch 4 (`IF-TOS-MMS-011/012`, `IF-TOS-EXT-041`, `IF-TOS-TWD-021`) | interface 영역 배정. 값 형식 `"<IF_ID> <이름>"` (이름이 없는 것도 있음) |
| `images` | swing Screen 2 (`ZROMRR0700090`, `ZROMRR0700340`, 출력 화면) | 이미지·문구 교체 요건 → screen `modify`, Minor (L-2) |
| `cron`, `cycle` | Batch 전부. tos 는 `steps`·`group` 추가 | 배치 주기 변경 요건 (L-1). swing 은 실제 주기가 `TB_BAT_JOB.CRON_EXPR` 데이터 |
| `code_grp`, `code_grp_nm`, `code_values` | swing Column 20 (tos 는 없음, 공통코드는 `TCP_CMM_CD` 테이블 데이터) | 코드값 추가 = 데이터 변경 (스키마 변경 아님) 판단 |
| `data_type`, `nullable`, `pk` | Column 전부 | 컬럼 길이·타입 변경 판단 |
| `is_common` | 11개 (아래 1-4) | 공통 노드 처리 |
| `url`·`http_method` (tos Controller), `stmt_type` (tos SQL), `dbio_type`·`used_by` (tos DBIO) | tos | hop 영향 설명 보조 |

### 1-4. 설계에 영향을 주는 특이 노드

| 구분 | 노드 | 처리 |
|---|---|---|
| 공통 (`is_common=true`) | swing `MOD:IfClient.send`, `MOD:BatchLauncher.runJob`, `MOD:AgeCalcUtil.*` 3개, `MOD:CommCdSvc.getCdNm/getCdAttr`, `TP:CommCdSvc.getCdList`, `DBIO:CommCdSql.selectCdList` / tos `CTL:CmmCdController.selectCdList`, `SQL:CmmCdMapper.selectCdList` | Explorer 가 확장하지 않는다 (`excluded_common`). 정답셋은 이 중 일부를 `reference_only`(H-2 `AgeCalcUtil`, L-1 `BatchLauncher`) 또는 경로 시작(SC-003 `CmmCdController`)으로 둔다 → Simulator 는 `unverified_segments` 에 남기고 Composer 가 개발 체크리스트 "공통 모듈 영향 확인" 으로 올린다 |
| 배치 기동기 공통 경로 | swing `LAUNCHER` 로 기동되는 Job → `DBIO:BatJobSql.updateLastRun` / `insertRunHst` | 모든 기동 Job 에 붙으므로 요건과 무관한 분기로 자주 나온다. 경로 제외 후보 1순위. 단 L-1(배치 주기)은 이 경로가 정답 |
| 고립 노드 (엣지 없음) | `CTL:LoginController.logout`, `SVC:CampMsgService.appendRejectFooter`, `BAT:TBCH1020`, `BAT:TBCH4020` | 경로가 1노드로 끝난다. `BAT:TBCH1020/4020` 은 인터페이스만 있고 처리 소스가 적재 범위 밖 → `unverified_segments` |
| 한 함수에 SQL 여러 개 | tos `SQL:<프로그램>.<함수>#<n>` 3개 (Pro*C) | ID 그대로 쓴다. Composer 경로 요약에서 `#n` 을 지우지 않는다 |
| 같은 TP 가 화면·배치 양쪽 | `TP:CombScrbSvc.joinComb` (Screen 과 `BAT:ZDOCBB0100040` 이 호출) 등 Batch→TP 2건 | TP 변경은 screen·batch 두 영역에 영향. 체크리스트에 "배치 동시 영향" 항목 |

---

## 2. Simulator (2-3)

**역할**: 복잡 요건에서 영역(screen / db / interface / batch)별 영향 경로를 구성하고, 구현 가능성·난이도·대안을 판정한다. 그래프에 없는 신규 요소는 별도로 제안한다.

**입력**

| 필드 | 사용 |
|---|---|
| `planner.items` | 요건 항목(`item_id`·`text`)·변경 유형(`change_type`) → 영역별 영향 유형 판단 |
| `explorer.paths` | 경로 골격. `nodes` 는 탐색 방향과 관계없이 진입 → DB 순서, `item_id` 로 요건 항목과 연결 |
| `explorer.edges`, `explorer.nodes` | hop 관계(엣지 유형·**`columns`**), 노드 역할·속성(`role`, `domain`, `properties`) |
| `explorer.item_results` | 목표 미도달(`reached=false`)·hop 상한(`frontier`)·매칭 실패(`unmatched_mentions`) → `unverified_segments` |
| `explorer.unresolved`, `explorer.excluded_common` | 분석 범위 밖·공통 노드 → `unverified_segments` |
| `retriever.evidence[]` (`content` 원문) | hop 근거(`evidence_ref`)·확신도, LLM 판단 근거 |
| `retriever.nodes_without_evidence` | 해당 hop 은 `inferred` |
| `replan_feedback` | 재계획 라운드면 이전 누락 사유 (같은 누락 반복 방지) |

**처리**

1. **경로 골격 (규칙, `rules.build_impact_paths`)**: `explorer.paths` 마다 `ImpactPath` 를 만든다.
   - hop: `node` = 경로 노드 ID, `relation` = 앞 노드 → 이 노드 엣지 유형(`explorer.edges`, 첫 hop 은 `ENTRY`), `evidence_ref` = 해당 노드 Evidence 의 `file_path#location` (예: `program/ProdScrbSvc.pgm#L40-88`)
   - `confidence`: Evidence 가 있으면 `confirmed`, 없으면 `inferred`
   - 같은 노드 순서의 경로는 하나로 합친다. 경로 id 는 `P<item_id>-<n>`
   - `cross_domain`: 경로 노드의 `domain` 이 둘 이상 (swing ↔ tos = 상품 SM ↔ 마케팅 SM, `rules.SM_PART_DOMAINS`)
   - **(v0.4) DB hop 의 컬럼 단위 정보**: DataAccess → Table hop 은 엣지 `columns` 를 LLM 입력에 같이 넣는다. 요건이 가리키는 컬럼이 `WRITES.columns` 에 있으면 변경 후보, `READS.columns` 에만 있으면 조회 영향, 둘 다 없으면 "컬럼 미사용"으로 표시한다 (PL/SQL DBIO 하나가 여러 테이블을 다루기 때문).
2. **영역 배정 (규칙)**: 0장 영역 분류 규칙으로 경로를 영역에 배정한다.
3. **영향 판정 (LLM)**: 영역별로 경로·원문·요건 항목을 주고 `SimulatorDraft` 를 받는다.
   - hop 별 `impact` (이 노드에서 무엇이 바뀌는지 / 확인만 필요한지)
   - 영역별 `impact_type`(add / modify / reference_only / none), `feasibility`, `difficulty` + `difficulty_basis`, `schedule_factors`
   - `feasibility=hard` 면 `infeasible_reason` 과 `alternatives`(가능성·난이도·장단점) 필수
   - 요청안과 대안이 갈리면 `requested_vs_alternative` 에 비교 요약 (예: SC-001 신규 컬럼 추가 vs 기존 광고여부 변환)
   - 요건과 무관한 경로는 **제외 제안**과 사유를 낸다 (4단계 가드 통과 시에만 제외)
   - **(v0.4) 신규 요소 제안**: 그래프에 없는 컬럼·DBIO·배치·코드 데이터가 필요하면 `new_elements` 로 낸다 (`suggested_id`·`area`·`reason`). 정답셋 `expected.new` 형식과 같다 (예: H-2 `COL:TB_PROD.ONE_PER_CUST_YN`, `BAT:ZPRDBB0100010` / SC-003 `TBL:TCP_CMM_CD (데이터 CP_OCO_POP_MSG)`). 신규 요소가 있는 영역은 `impact_type=add` 를 쓸 수 있다
   - LLM 입력의 노드 설명은 `desc` → 없으면 `program_desc`·이름 → 그래도 없으면 `code` 앞부분 (1-3 채움률)
4. **판정 보정 (규칙)**: LLM 결과를 그대로 믿지 않는다.
   - LLM 이 그래프에 없는 노드를 hop 에 넣으면 버린다 (경로는 골격 노드만). **같은 ID 가 `new_elements` 에도 없으면 `new_elements` 로 옮기지 않고 버린다** (LLM 이 기존 노드 ID 를 잘못 쓴 경우와 구분)
   - `new_elements.suggested_id` 가 이미 그래프에 있는 노드면 신규가 아니므로 버린다
   - 영역의 hop 이 전부 `inferred` 이면 `feasibility` 를 `feasible` 로 확정하지 않고 `review`
   - 영역 `confidence` = hop 이 모두 `confirmed` 일 때만 `confirmed`
   - **경로 제외 가드**: 아래에 하나라도 해당하면 제외 제안을 거절하고 경로를 남긴다.
     - 목표 역할에 도달한 경로(`stop_reason=target_reached`)의 마지막 노드에 근거(`confirmed`)가 있음
     - 사용자가 확정한 노드(`Mention.confirmed_node`)가 경로에 있음
     - 그 경로를 빼면 요건 항목(`item_id`)의 경로가 하나도 남지 않음
   - 제외한 경로는 `dropped_paths` 에 사유와 함께 남기고, Composer 가 레포트에 "검토 제외 경로"로 보여준다 (사람이 확인 가능)
5. **분석 한계**: `explorer.unresolved`, `item_results` 의 미도달·frontier·매칭 실패, 근거 없는 노드, 공통 제외 노드, **(v0.4) 고립 노드로 끝난 경로**(1-4), **swing 컬럼 변경 시 참조 테이블 미확인**(swing 에 `REFERENCES` 없음)을 `unverified_segments` 에 넣는다.
6. **LLM 없음**: 3단계를 건너뛰고 `impact` = "영향 검토 필요", `impact_type` = 경로 있음 `modify` / 없음 `none`, `feasibility=review`, `difficulty` = 아래 기준의 규칙 판정, `new_elements` 는 비운다. 경로는 제외하지 않는다.

**경로 제외를 허용하는 이유**: Explorer 경로에는 요건과 무관한 분기가 섞인다 (yj 시나리오 기준 노드 정밀도 0.2 수준). 현재 그래프에서는 특히 swing 배치 기동기 경로(`DBIO:BatJobSql.*`)와 공통 코드 조회가 많이 섞인다. 모두 레포트에 올리면 검토 부담이 커진다. 대신 KPI 의 최우선 목표가 Critical 미탐 0건이므로 가드 규칙으로 미탐 위험을 막고, 제외 내역은 사람이 볼 수 있게 남긴다.

### 난이도 판단 기준 (v0.4 조정안)

변경(`impact_type` = add / modify)이 닿는 노드의 그래프 단계([data_structure_neo4j.svg](data_structure_neo4j.svg))로 영역 난이도를 정하고, 영역 수·도메인 연계는 요건 전체 난이도에만 반영한다.

영역 난이도 (`AreaImpact.difficulty`, `difficulty_basis` 에 적용 항목):

| 난이도 | 기준 (하나라도 해당) | 그래프 단계 · 판단 근거 |
|---|---|---|
| Major | 컬럼 **길이·타입** 변경 · 신규 테이블 · 인터페이스 전문 레이아웃 변경 · `REFERENCES` 로 연결된 컬럼 변경 | 5 Column `data_type` 변경, `REFERENCES.via`, interface `modify` |
| Medium | 기존 테이블에 **컬럼 추가** (v0.4 에서 Major → Medium) · 업무 로직 변경이 2개 이상 노드에 걸침 · 화면 항목 추가 + 저장 로직 변경 · 신규 DBIO | `new_elements` 의 `COL:`·`DBIO:`, 2~3 Logic·DataAccess 다수 |
| Minor | 데이터·설정값·문구·이미지 변경 · 공통코드 값 추가 · 배치 주기(`cron`) 변경 · 단일 노드 변경 | Screen `images`, Column `code_values`, Batch `cron`, 4 Table 데이터만 |

요건 전체 난이도 (`simulator.requirement_difficulty`, Composer 요약·정답셋 `difficulty` 비교): 영역 난이도 중 최고. 단 변경 영역 3개 이상 · 도메인 간 연계 → Major, **Medium 이상 영역이 2개 → Medium 이상** (v0.4: Minor 영역끼리는 개수로 올리지 않음).

**정답셋 대조 (정답 `expected.areas`·`new` 에 기준표를 손으로 적용한 결과, 실행 결과 아님)**

| 케이스 | 정답 | v0.3 기준 | v0.4 기준 | 어긋난 이유 / 조정 |
|---|---|---|---|---|
| L-1 배치 주기 | Minor | Medium (변경 영역 2개: db·batch) | Minor | 둘 다 설정 데이터 변경 → 영역 수 규칙을 Medium 이상 영역에만 |
| L-3 그리드 항목 추가 | Minor | Medium (screen·db) | Minor | 기존 컬럼 조회 추가, 각 영역 단일 노드 |
| M-3 판촉품 지급 체크 | Medium | Major (컬럼 추가) | Medium | 기존 테이블 nullable 컬럼 추가 → Medium |
| M-4 결합상품 개선 | Medium | Major (컬럼 추가) | Medium | 같음 |
| SC-002 권한 만료 장애 | Major | Medium (db·batch) | Medium | **미해결.** 배치 신규 SQL(add) + 권한 데이터 정정. 운영 장애 성격이 난이도에 반영됨 → 11장 |
| 나머지 11건 | — | 일치 | 일치 | H-1·H-2 는 변경 영역 3개 → Major 유지 |

일정 영향 요인: 영역당 3개 이내 (Simulator), 레포트 전체 8개 + "외 n건" (Composer).

**출력 `SimulationOutput`**: `area_impacts[AreaImpact]` (+ `new_elements`, v0.4), `requested_vs_alternative`, `unverified_segments`, `dropped_paths`

**KPI 연결**: 복잡 flow 의 평가 노드는 `area_impacts[].paths[].hops[].node` 다 (`dropped_paths`·`new_elements` 제외). 재계획이 있었으면 마지막 라운드 결과가 평가된다. `new_elements` 는 정답 `expected.new` 와 별도로 비교한다 (9장).

---

## 3. Verifier (2-4)

**역할**: Simulator 결과의 영역 누락과 근거를 검증하고, 보완이 필요하면 재계획을 요청한다 (다이어그램의 빨간 점선 → Supervisor).

**입력**: `simulation`, `planner.items`, `explorer`(경로·item_results·unresolved), `retriever.evidence`, `replan_feedback`

**점검 항목 (`VerifyCheck.check`)**

| check | 방식 | 보완 필요 조건 |
|---|---|---|
| `area_path_missing` (영역 누락) | 규칙 | 요건 항목의 변경 유형이 가리키는 영역에 경로가 없음 (아래 표) / 항목의 경로가 하나도 없음 (`item_results.reached=false` 이고 경로 0) |
| `evidence_missing` (근거 검증) | 규칙 | `confirmed` hop 의 `evidence_ref` 가 Retriever 근거에 없음 / `modify`·`add` 영역의 hop 이 전부 `inferred` (단 `add` 영역이 `new_elements` 만으로 이루어졌으면 제외) |
| `cross_domain_link` | 규칙 | 경로 노드의 domain 이 섞였는데 `cross_domain=false` |
| `redundant_path` | 규칙 | 같은 노드 순서의 경로가 중복되거나, 한 경로가 다른 경로에 완전히 포함됨 (경고만, 재계획 사유 아님) |
| `alternative_basis` | 규칙 + LLM | `hard` 영역에 대안이 없음 / 대안 설명이 근거(Evidence·노드)를 가리키지 않음 |

변경 유형 → 반드시 경로(또는 그래프 밖 표시)가 있어야 할 영역 (`rules.CHANGE_TYPE_RULES` 의 목표 역할과 맞춤):

| change_type | 목표 역할 (yj) | 필수 영역 |
|---|---|---|
| screen_change | EntryHandler | screen (Screen 단독 경로로 충족, L-2·L-4·SC-005) |
| query_field_add | DataAccess | screen, db |
| data_add | Table (forward_then_reverse) | db |
| column_spec_change | Entry (reverse) | db |
| batch_change | DataAccess | batch |
| interface_field_add | Entry (reverse) | interface (그래프 밖이면 `unresolved` 표시로 충족) |
| logic_change, ops_cause, structure_query | Logic / Table / Column | (점검 안 함. SC-002·SC-006 처럼 Table 에서 시작하는 경로가 정상) |

**v0.4 추가 점검 (경고, 재계획 사유 아님)**: 화면 경로에 있는 TP 가 Batch→TP 엣지로 배치에서도 호출되는데 batch 영역이 `none` 이면 `checks[].note` 에 남긴다 (H-1 `TP:CombScrbSvc.joinComb`). Composer 가 체크리스트로 올린다.

**판정**

- 하나라도 보완이 필요하면 `result=needs_supplement`, `reason`(무엇이 왜 빠졌는지)과 `additional_scope` 를 쓴다.
  - `additional_scope` 는 Planner 가 탐색 명세로 바꿀 수 있는 형태로 쓴다: `node:<노드ID>` (이어서 볼 노드) / `area:<영역>` / `keyword:<표현>`. Planner 는 `[이전 탐색 명세]` + `[재계획 요청]` 블록으로 받는다 (`planner.user_message`).
  - Supervisor 가 `replan_feedback` 에 누적하고 Planner 로 되돌린다 (Reflexion). `MAX_REPLAN`(2) 을 넘으면 그대로 Composer 로 간다.
- 같은 사유가 이전 라운드 `replan_feedback` 에 이미 있으면 재계획해도 해결되지 않는 것으로 보고 `pass` 로 넘기되, 사유를 `checks[].note` 에 남긴다 (Composer 가 분석 한계로 표시).
- 고립 노드(1-4)로 끝나 경로가 이어지지 않는 영역은 재계획해도 새 경로가 나오지 않으므로 `additional_scope` 에 넣지 않고 분석 한계로 넘긴다.
- 재계획 한도 초과로 넘어간 미해결 점검도 Composer 가 분석 한계에 쓴다.
- **LLM 없음**: `alternative_basis` 의 근거 판단만 건너뛴다 (대안 유무만 규칙 점검).

**출력 `VerifierOutput`**: `result`, `checks[VerifyCheck]`, `reason`, `additional_scope`

---

## 4. Composer (3)

**역할**: 분석 결과를 사용자 결과물로 만든다. 단순 → 단순 요건 응답, 복잡 → 복잡 요건 결과물(레포트·체크리스트), 요청 시 회신 메일 초안·MD/PDF.

**입력**

| flow | 사용 |
|---|---|
| 단순 (Retriever → Composer) | `planner.structured_request`, `planner.items`, `explorer.paths`, `retriever.evidence`, `retriever.nodes_without_evidence`, `explorer.unresolved` |
| 복잡 (Verifier → Composer) | 위 + `simulation`, `verifier` (미해결 점검) |
| 공통 | `user_input.output_options`, `followups` (후속 답변), 이전 `composer` (재작성 시) |

### 4-1. 단순 요건 응답 (`SimpleAnswer`)

다이어그램: **요약 결론 · 영향 경로 · 근거**

| 필드 | 작성 |
|---|---|
| `conclusion_lines` (3~5줄) | LLM. 결론 먼저, 노드명·테이블명을 붙여 쓴다 |
| `impact_paths` | 코드. `explorer.paths` → `ImpactPath` (Simulator 1단계와 같은 함수, `impact` 는 LLM 이 한 줄로) |
| `evidence_table` | 코드. Evidence 마다 `claim`(LLM 요약 또는 노드 설명) · `evidence`(file_path) · `location` · `confidence` |
| `check_queries` | LLM. 운영 문의일 때 확인용 SQL. **코드에서 SELECT 단일 문장만 통과**시키고 나머지는 버린다. 컬럼 코드값 해석은 Column `code_values`(swing) / `TCP_CMM_CD` 조회(tos)를 쓴다 (SC-006) |
| `analysis_limits` | 코드. `explorer.unresolved`, `nodes_without_evidence` |
| `detail_analysis_recommended` | 규칙 + LLM. 변경 유형이 add/modify 이거나 LLM 이 변경 요소를 찾으면 `true` |

### 4-2. 복잡 요건 결과물 (`ComplexReport`)

다이어그램: **레포트(영역별 영향·가능성·대안) · 체크리스트(업무·개발 확인 항목) · 회신 메일·PDF(요청 시)**

| 필드 | 작성 |
|---|---|
| `summary_conclusion` | LLM. 가능 여부·난이도·핵심 영향을 3~5문장 |
| `area_verdicts` | 코드. `simulation.area_impacts` 를 영역별 한 행으로 (경로 요약은 "진입 → … → 끝 노드") |
| `new_elements` (v0.4) | 코드. 영역별 `new_elements` → 레포트 "신규 개발 요소" 절 (ID·영역·사유). 확정 전이므로 개발 체크리스트 항목도 만든다 |
| `schedule_factors` | 코드. 영역별 `schedule_factors` 합치기 (중복 제거) |
| `path_details` | 코드. `simulation` 경로 그대로 (같은 경로는 한 번만) |
| `analysis_limits` | 코드. `simulation.unverified_segments` + Verifier 반복·경고 점검 + 재계획 한도 초과 사실 |
| `dropped_paths` | 코드. `simulation.dropped_paths` → 레포트 "검토 제외 경로" 절 + 개발 체크리스트 항목 |
| `checklist` | LLM 초안 + 코드 정리. 아래 규칙 |
| `stage` | `initial` → (후속 답변 반영) `updated` → (체크리스트 전부 확정) `final_for_dev` |

**체크리스트 규칙**

- `owner=business`: 정책·업무 결정이 필요한 질문 (예: 상품코드 분리 여부, 할인 정책). `owner=developer`: 구현 방식·테스트 범위 확인 (예: 하드코딩 위치, 함께 영향받는 배치).
- 출처: `planner.structured_request.open_items`, `inferred` hop, `review` 영역, 대안 선택이 필요한 지점, 검토 제외 경로, **(v0.4) 신규 요소, 공통 노드 영향(`excluded_common`), 화면·배치 공유 TP (Verifier note)**.
- `item_id` 는 `B-1, B-2… / D-1, D-2…`. 후속 답변(`FollowupInput.target_item_ids`)은 이 id 로 연결한다.

### 4-3. 후속 입력과 재분석

**현재 Supervisor 동작 (yj)**: `Supervisor.followup(session_id, FollowupInput)` 이 같은 세션 상태에 후속 입력을 붙이고 (최근 `MEMORY_WINDOW_N` 개), `_route_start` 가 유형으로 진입점을 고른다.

| 후속 입력 유형 | 진입 | 결과 |
|---|---|---|
| `business_answer`, `developer_opinion`, `query_result` | Composer | Composer → KPI. 분석 결과는 그대로, 결과물만 갱신 |
| `change_analysis`, `reanalysis` | Planner | Planner 가 `[후속 입력]` 을 보고 탐색 명세부터 다시 → 전체 flow |

`ComposerOutput.reanalysis_request` 는 Supervisor 가 읽지 않는다 (라우팅 없음).

**Composer 설계**

1. `target_item_ids` 의 체크리스트 항목에 `answer` 를 채우고 `status=confirmed`. `stage` 를 갱신한다.
2. `query_result` 는 단순 응답의 `query_result_summary`(코드 의미·주요 원인·문의 항목)로 정리한다.
3. 답변 때문에 영향 경로 자체가 바뀌어야 하면 (예: "B 를 별도 상품코드로 관리") `reanalysis_request`(사유·바뀐 가정)를 반환한다.
   - Composer 가 자동으로 재분석을 돌리지 않는다. UI 가 요청을 보여주고 사용자가 확인하면 `FollowupInput(type=reanalysis, content=바뀐 가정)` 으로 보낸다 → 기존 Planner 진입 흐름을 그대로 쓴다.
   - **Supervisor 변경 없음.** 자동 재분석이 필요해지면 그때 yj 와 합의한다 (11장).

### 4-4. 추가 산출물 (`UserInput.output_options`)

- `mail_draft`: 회신 메일 초안 (`subject`, `body`). 결론 → 영향 영역 → 확인 요청(business 체크리스트) 순서. 수신자는 비워 둔다.
- `pdf`: `save_report(format="pdf")` → `report_file_path`. MD 는 이미 구현, PDF 변환(`markdown` + `weasyprint`, `report` 옵션 묶음)은 Composer 작업에서 구현한다.
- `markdown`: 항상 `templates/simple_answer.md.j2` / `complex_report.md.j2` 로 렌더링한다. LLM 이 markdown 을 직접 쓰지 않는다. 레포트 템플릿에 "검토 제외 경로"·**"신규 개발 요소"(v0.4)** 절을 둔다.
- **LLM 없음**: 결론·요약은 규칙 문구(영역·경로 수·판정 나열), 체크리스트는 open_items 와 `review` 영역에서만 만든다.

---

## 5. 연결 (Supervisor · KPI)

| 연결 | 내용 | 현재 코드 |
|---|---|---|
| Retriever → Simulator | 복잡이고 plan 에 simulator 가 있으면 | ✅ `_after_explore` |
| Simulator → Verifier | plan 에 verifier 가 있으면 (없으면 Composer) | ✅ `_next_after_sim` |
| Verifier → 재계획 | `needs_supplement` + `replan_count < MAX_REPLAN` → `replan_feedback` 누적 → Planner. 새 라운드는 Explorer·Retriever 를 새로 실행 | ✅ `_verifier_node`, `_route_verifier` |
| Verifier → Composer | 통과 또는 재계획 한도 초과 | ✅ |
| Retriever → Composer | 단순 | ✅ |
| Composer → KPI (점선) | Composer 다음 실행. KPI 결과는 Composer 에 전달하지 않는다 | ✅ `COMPOSER → KPI → END` |
| 후속 답변 → Composer / 재분석 → Planner | `Supervisor.followup` | ✅ |
| Composer `reanalysis_request` | 사용자 확인 후 `reanalysis` 후속 입력으로 (4-3) | ✅ (Supervisor 변경 없음) |

- 재계획 라운드에서 `simulation`·`verifier` 는 다음 라운드 결과로 덮어쓴다. KPI 는 마지막 라운드를 평가한다.
- `analyze(..., until="simulator" | "verifier")` 로 단계별 확인이 가능하다 (Composer·KPI 생략).

---

## 6. 앞단 에이전트와의 정합성

yj 쪽 출력 필드를 그대로 쓰고, yj 쪽 기준표를 다시 정의하지 않는다.

| 앞단 결정 | 이 설계에서 쓰는 방식 |
|---|---|
| Planner `items[ExploreItem]` (항목 단위 탐색 명세, `change_type`) | 경로 id·영역 필수 점검·체크리스트를 요건 항목 단위로 연결 |
| `rules.CHANGE_TYPE_RULES` (변경 유형 → 목표 역할·방향) | Verifier 필수 영역 표를 이 표의 목표 역할에 맞춤 |
| `rules.SM_PART_DOMAINS` (SM → swing/tos) | `cross_domain` 판단 (domain 이 둘 이상 = SM 파트를 넘나듦) |
| `rules.MENTION_LABELS` (interface 언급 → Batch·TP·Module) | 현재 그래프에서 `interfaces` 속성은 TP·Batch 에만 있음 (1-3). Module 은 후보에 남아도 무방 |
| `NodeRole` (Entry ~ Column) | 영역 분류 (라벨 대신 역할) |
| Explorer `paths` (진입 → DB 순서), `edges`(속성 포함), `item_results`, `unresolved`, `excluded_common` | 경로 골격·관계·컬럼 단위 영향·분석 한계 |
| Retriever `Evidence { file_path, location, source_kind, content }` | `evidence_ref` = `file_path#location`, interface 근거, LLM 판단 원문 |
| Supervisor 재계획 (`replan_feedback`, Reflexion) | Verifier `reason`·`additional_scope` 형식, 반복 사유 처리 |
| Supervisor 후속 입력 라우팅 | Composer 후속 답변·재분석 요청 처리 (4-3) |
| KPI `final_nodes` (복잡 = Simulator 경로, 단순 = Retriever 결과) | `PathHop.node` = 그래프 ID, `dropped_paths`·`new_elements` 는 평가 제외 |
| 그래프 파서 (`scripts/graph/common.py` `BaseParser` → `parse_swing.py`·`parse_tos.py`) | 노드 ID·라벨·엣지 규칙은 [mock_data.md](mock_data.md) 기준. 이 설계는 파서 출력만 소비 |

---

## 7. 스키마 변경

| 모델 | 변경 | 상태 | 이유 |
|---|---|---|---|
| `VerifyCheck.check` (`CheckKind`) | + `evidence_missing` | 반영 | 다이어그램의 "근거 검증" 항목 |
| `SimulationOutput` | + `dropped_paths: list[DroppedPath{path, reason}]` | 반영 | 요건과 무관해 제외한 경로 (가드 통과분) |
| `ComplexReport` | + `dropped_paths: list[DroppedPath]` | 반영 | 레포트 "검토 제외 경로" 절 |
| `PathHop.evidence_ref` | 설명 `file_path#location` | 반영 | Retriever Evidence 의 `location`(라인 범위)과 맞춤 |
| `AreaImpact` | **+ `new_elements: list[NewElement{suggested_id, area, reason}]`** | v0.4 신규 | 그래프에 없는 신규 요소. 정답셋 `expected.new` 와 같은 형식 |
| `ComplexReport` | **+ `new_elements: list[NewElement]`** | v0.4 신규 | 레포트 "신규 개발 요소" 절 |
| `SimulatorDraft` | **+ `new_elements`** | v0.4 신규 | LLM 이 신규 요소를 제안하는 자리 |
| 신규 (LLM 응답용, `schemas.json` 비공개) | `SimulatorDraft`, `VerifierDraft`, `ComposerDraft` | 반영 | Structured Output. 최종 출력은 코드가 조립 |

변경 후 `python scripts/export_schema.py --write`, `tests/test_schema_sync.py` 로 확인한다. `AreaImpact`·`ComplexReport` 는 프론트 목업(`frontend/src/mocks/fixtures/scenarios.ts`)에도 반영한다.

---

## 8. 프롬프트 (가안)

| 에이전트 | 기법 | 내용 |
|---|---|---|
| Simulator | Few-shot | 판정 예시 2개 (feasible·Minor 1건, 대안이 있는 Major 1건: SC-001 형태) + **신규 요소 예시 1개** (v0.4). 예시는 가상 노드로 쓴다 (정답셋 노드 사용 금지, yj 프롬프트와 같은 원칙) |
| Verifier | 규칙 우선 | LLM 은 `alternative_basis` 판단에만 사용. 점검 결과는 항상 근거 노드·필드를 가리킨다 |
| Composer | 템플릿 고정 | LLM 은 문장 필드만. 톤은 `_common.md` (결론 먼저, 확인/추정 구분) |

- 모든 프롬프트 앞에 `_common.md` 가 붙는다 (BaseAgent).
- 입력의 소스·문서 원문은 길이 상한을 둔다 (노드당 원문 일부 + 전체 문자 수 상한). swing Batch `code` 는 Job 정의 + 실행 함수 원문이 합쳐져 길다 → Job 정의 부분을 먼저 자른다.
- Simulator 입력에 DB hop 의 엣지 `columns`, Screen `images`, Batch `cron`/`cycle`, Column `data_type`·`code_values` 를 노드 속성 한 줄로 넣는다 (난이도 기준의 판단 근거).

---

## 9. 테스트·검증

**pytest (LLM·Neo4j 없음)**: 가짜 LLM(`Fixed`) 또는 LLM 없음으로 실행. tos·swing 노드를 각각 쓰는 케이스를 둔다.

| 대상 | 확인 |
|---|---|
| Simulator | 경로 골격 (노드 순서·엣지 유형·evidence_ref·확신도), 역할 기준 영역 배정 (tos/swing), **v0.4 경로 모양 (`OPENS`·Batch→TP·Screen 단독·Table 시작·고립 노드)**, 그래프 밖 노드 제거, **`new_elements` 보정 (기존 노드 ID 거절)**, inferred 영역은 review, 경로 제외 가드 3종, **난이도 v0.4 기준 (컬럼 추가 Medium, Minor 영역 2개 Minor)**, LLM 없음 출력 |
| Verifier | 점검 항목별 보완 필요/통과 (`evidence_missing` 포함), 필수 영역 표, `additional_scope` 형식, 같은 사유 반복 시 pass, 화면·배치 공유 TP 경고, 재계획 한도 (`test_supervisor`) |
| Composer | 단순/복잡 필드 조립, SELECT 외 쿼리 차단, 체크리스트 id·후속 답변 반영·stage 전이, `reanalysis_request`, 검토 제외 경로·신규 개발 요소 표시, markdown 렌더링 (`test_templates`) |
| 전체 | `Supervisor.run` 이 Composer·KPI 까지 끝나고, 복잡 flow 도 KPI `evaluable=true` |

**정답셋 대조 (`eval/ground_truth`, 16건)**

| 구분 | swing (상품 SM, 10건) | tos (마케팅 SM, 6건) |
|---|---|---|
| 난이도 | Major 2 (H-1·H-2), Medium 4 (M-1~4), Minor 4 (L-1~4) | Major 2 (SC-001·002), Medium 2 (SC-003·004), Minor 2 (SC-005·006) |
| interface | M-2 `reference_only` | SC-001 `modify` (유일한 interface 변경) |
| batch `add` | H-2 | — |
| `expected.new` 있음 | H-1, H-2, M-3, M-4 | SC-002, SC-003 |
| feasibility | 전부 feasible | 전부 feasible |

정답셋의 아래 필드를 에이전트별 기대값으로 쓴다 (KPI 설계서 6.3 확장 KPI 후보와 연결).

| 정답 필드 | 비교 대상 |
|---|---|
| `expected.areas` (영역별 impact_type) | Simulator `area_impacts[].impact_type` |
| `expected.feasibility` | Simulator 영역 판정 |
| `difficulty` | Simulator 요건 전체 난이도 (2장 대조표) |
| `expected.new` | Simulator `new_elements` (v0.4. ID 완전 일치가 아니라 영역 + 대상 테이블/프로그램 일치로 본다. 정답 ID 가 "예시"인 경우가 있음) |
| `expected.reference_only` | `reference_only` hop / 공통 노드 체크리스트 |
| `conclusion.correct / wrong`, `wrong_paths` | Simulator `requested_vs_alternative`, 오답 경로 채택 여부 |
| `expected.open_items` (owner·question) | Composer 체크리스트 |
| `analysis_limits` | Composer 분석 한계 |

---

## 10. 구현 순서

v0.3 까지의 1~4단계(공통·Simulator·Verifier·Composer 1차)는 반영되어 있다. v0.4 는 아래 순서로 반영한다.

1. **스키마**: `NewElement`, `AreaImpact.new_elements`, `ComplexReport.new_elements`, `SimulatorDraft.new_elements` → `export_schema.py --write`, 프론트 목업 갱신
2. **Simulator**: 난이도 규칙 조정(`rule_difficulty`·`requirement_difficulty`), 엣지 `columns`·노드 속성 LLM 입력, `new_elements` 보정, 고립 노드·swing REFERENCES 분석 한계
3. **Verifier**: `evidence_missing` 의 `new_elements` 예외, 화면·배치 공유 TP 경고, 고립 노드 재계획 제외
4. **Composer**: "신규 개발 요소" 절·체크리스트, 공통 노드 체크리스트, 코드값 해석(`code_values`)
5. **검증**: `run_scenarios.py` 로 16건 실행 → `scripts/eval_kpi.py` 로 KPI, 2장 난이도 대조표를 실제 실행 결과로 갱신

---

## 11. 미결 항목

| # | 항목 | 비고 |
|---|---|---|
| 1 | 난이도 기준 검증 | v0.4 조정안은 정답셋에 손으로 적용한 결과. 실행 후 재확인. **SC-002(운영 장애, 정답 Major)는 기준표로 설명이 안 됨** → 운영 장애·권한 요건을 별도 가중할지 결정 |
| 2 | 변경 유형 → 필수 영역 | logic_change 등 점검 제외 유형을 둘지 |
| 3 | 자동 재분석 | 지금은 사용자 확인 후 `reanalysis` 후속 입력. 자동화가 필요하면 Supervisor 에 `reanalysis_request` 라우팅 추가 (yj 합의) |
| 4 | 두 시스템에 걸친 요건 | Planner 탐색 영역이 SM 선택 하나로 고정. 현재 그래프에도 swing ↔ tos 엣지가 없음. 필요하면 `SM_PART_DOMAINS` 확장 (yj 합의) |
| 5 | LLM 입력 길이 상한 | 노드당 원문 길이, 전체 상한, 요약 여부. swing Batch `code` 가 특히 김 |
| 6 | PDF 변환 방식 | weasyprint 한글 폰트, 사내 환경 설치 가능 여부 |
| 7 | 정답셋에 `hard` 사례 없음 | 16건 모두 feasible. 정답셋은 바꾸지 않고, 대안·infeasible 판정은 `tests/` 의 가상 노드 fixture 로 검증 |
| 8 | 확인 쿼리 DB 차이 | swing SQL 이 PL/SQL 패키지(Oracle)로 바뀜. tos 는 MyBatis·Pro*C. 확인 쿼리 문법 차이 확인 |
| 9 | 공통 노드 처리 (v0.4) | 정답셋이 공통 노드를 `reference_only`·경로 시작으로 쓰는 경우가 있음 (H-2·L-1·SC-003). Explorer 의 공통 제외 규칙과 맞출지 yj 합의 |
| 10 | swing 정의서 채움률 (v0.4) | DBIO `desc` 15/60 등. 데이터는 그대로 두고 LLM 이 `code` 원문으로 판단 (2장 처리 3). 설명 없는 노드가 판정 품질에 주는 영향을 실행 결과로 확인 |
| 11 | `new_elements` KPI (v0.4) | 정답 ID 가 예시 수준이라 일치 기준(영역 + 대상 테이블/프로그램)을 KPI 설계서에 반영할지 |
