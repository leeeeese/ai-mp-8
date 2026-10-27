# Agent 설계서: Simulator · Verifier · Composer (에이전트 단위)

> 담당: **2-3 Simulator · 2-4 Verifier · 3 Composer · KPI Agent** (본인) / Supervisor · 1 Planner · 2-1 Explorer · 2-2 Retriever · UI (파트너, [YJ_agent_design.md](YJ_agent_design.md))
> 스키마·필드명은 가안이며 구현·정답셋 대조 결과에 따라 조정한다. **공용 데이터(`data/` 소스·정의서·그래프, `eval/ground_truth`)는 바꾸지 않는다** — 데이터의 빈틈은 에이전트 규칙·분석 한계로 처리한다.

## 0. 한눈에 보기

![Agent 워크플로우](agent_workflow.svg)

- 흐름: … 2-2 Retriever → (복잡) 2-3 Simulator(영역별 영향·가능성·난이도·대안) → 2-4 Verifier(영역 누락·근거 검증, 보완 필요 시 Supervisor → Planner 재계획) → 3 Composer. 단순은 Retriever → 3 Composer.
- KPI Agent (점선, 평가 PoC): Composer 이후 실행 결과·실행 로그를 정답 노드 목록(`AnswerKey`)과 비교한다. 결과는 Composer 에 전달하지 않는다. 상세는 [SE_kpi-agent-design.md](SE_kpi-agent-design.md).
- 후속 입력(현업 답변·개발 의견·조회 결과)은 Composer 로 바로 와서 결과물만 갱신하고, 변경 분석·재분석 요청은 Planner 부터 다시 돈다.

```
… 2-2 Retriever ─(단순)──────────────────────────────────────────┐
        │(복잡)                                                   ▼
      2-3 Simulator  규칙+LLM  경로 골격 → 영역 배정 → 영향·가능성·난이도·대안 → 보정·가드
      2-4 Verifier   규칙+LLM  영역 누락 · 근거 검증 ─ 보완 필요 → Supervisor → Planner (재계획 ≤ 2회)
        │ 통과 · 재계획 한도 초과                                   ▼
      3 Composer     코드+LLM  단순 응답 / 레포트·체크리스트·회신 메일·MD/PDF (Jinja2)
        ┆ (평가, PoC)
      KPI Agent      규칙      정답 노드 목록 + 실행 로그 → 탐지율·미탐률·정밀도
```

| 에이전트 | LLM | 입력 (AgentState) | 출력 | 구현 |
|---|---|---|---|---|
| Simulator | gpt-6.1-sol (영향·판정) | planner, explorer, retriever, replan_feedback | `SimulationOutput` | `agents/simulator.py`, `prompts/simulator.md`, `rules.py` |
| Verifier | gpt-6.1-sol (대안 근거만) | planner, explorer, retriever, simulation, replan_feedback | `VerifierOutput` | `agents/verifier.py`, `prompts/verifier.md` |
| Composer | gpt-6.1-sol (문장 필드만) | 전체 + followups | `ComposerOutput` | `agents/composer.py`, `prompts/composer.md`, `templates/*.md.j2`, `tools/report.py` |
| KPI Agent | - | answer_key, 단계별 출력, agent_runs | `KpiOutput` | `agents/kpi.py`, `scripts/eval_kpi.py` |
| Supervisor · Planner · Explorer · Retriever | (파트너) | | | [YJ_agent_design.md](YJ_agent_design.md) |

### 설계 기술 반영 (상세 설계서 "핵심 에이전트 기술 스택" 중 담당 범위)

| 구분 | 기술 | 반영 위치 |
|---|---|---|
| 페르소나 | 시스템 공통(`_common.md`, BaseAgent 가 모든 프롬프트 앞에 붙임) + 에이전트별 역할 | `prompts/{simulator,verifier,composer}.md` |
| Simulator | Few-shot (Brown et al. 2020): 판정 예시 2개 (feasible·Minor 1건, 대안이 있는 Major 1건). 예시는 가상 노드만 (정답셋 노드 금지) | `prompts/simulator.md` |
| Verifier | 규칙 우선: 점검은 코드, LLM 은 `alternative_basis`(대안이 근거를 가리키는지)만 | `agents/verifier.py`, `prompts/verifier.md` |
| Verifier·Supervisor | Reflexion (Shinn et al. 2023): 보완 사유를 `reason`·`additional_scope` 로 내고 Supervisor 가 `replan_feedback` 에 누적 | `agents/verifier.py` → `supervisor._verifier_node` |
| Composer | 템플릿 고정: LLM 은 문장 필드만, markdown 은 항상 Jinja2 템플릿 렌더링 | `templates/simple_answer.md.j2`, `templates/complex_report.md.j2` |
| Output Parsing | Structured Output (Pydantic): `SimulatorDraft`, `VerifierDraft`, `SimpleDraft`·`ComplexDraft`·`FollowupDraft`. 최종 출력 모델은 코드가 조립 | 각 agent 모듈 |

- **규칙 먼저, LLM 은 보완.** 노드·경로·근거 연결처럼 사실 확인이 되는 부분은 코드로 만들고, 영향 설명·판정 사유·결론 문장만 LLM 이 쓴다 (Explorer·Retriever 와 같은 방식).
- **LLM 없이도 끝까지 실행.** LLM 키가 없거나 호출이 실패하면 규칙 결과만으로 출력 (판정 `review`, 문장은 규칙 문구). pytest·KPI 배치 평가가 LLM 없이 돈다. LLM 은 `llm.analysis_llm()` 으로 얻는다.
- **노드는 그래프 ID.** `PathHop.node` 는 반드시 `<접두어>:<키>` (복잡 flow KPI `final_nodes` = Simulator 경로 노드). 그래프에 없는 노드는 hop 이 아니라 `new_elements` 로 낸다 (예정).
- **라벨이 아니라 역할로 판단.** tos(Controller·Service·SQL·DBIO)·swing(TP·Module·DBIO) 차이는 `GraphNode.role`(`NodeRole`) 로 흡수한다.
- **확인과 추정 구분.** 근거(Evidence)가 있는 hop 만 `confirmed`, 나머지는 `inferred`. 근거 없이 구현 가능 여부를 확정하지 않고 `review`.
- **실행 로그.** LLM 을 쓰면 `self.last_usage` 에 토큰 사용량을 남긴다 (Supervisor 가 `AgentRunRecord` 에 기록).

상수: `MAX_REPLAN` 2 (`schemas/schemas.json#/constants`), Simulator `MAX_CONTEXT_PATHS` 40 · `MAX_CONTEXT_CHARS` 16000 · `MAX_SCHEDULE_FACTORS` 3 (영역당), Verifier `MAX_SCOPE` 5, Composer `MAX_SCHEDULE_FACTORS` 8 (레포트 전체) · `MAX_CONTEXT_EVIDENCE` 40.

---

## 1. Simulator (2-3)

**역할**: 복잡 요건에서 영역(screen / db / interface / batch)별 영향 경로를 구성하고, 구현 가능성·난이도·대안을 판정한다. 그래프에 없는 신규 요소는 별도로 제안한다.

**입력**

| 필드 | 사용 |
|---|---|
| `planner.items` | 요건 항목(`item_id`·`text`)·변경 유형(`change_type`) → 영역별 영향 유형 판단 |
| `explorer.paths` | 경로 골격. `nodes` 는 탐색 방향과 관계없이 진입 → DB 순서, `item_id` 로 요건 항목과 연결 |
| `explorer.edges`, `explorer.nodes` | hop 관계(엣지 유형·`columns`), 노드 역할·속성(`role`, `domain`, `properties`) |
| `explorer.item_results` | 목표 미도달(`reached=false`)·hop 상한(`frontier`)·매칭 실패(`unmatched_mentions`) → `unverified_segments` |
| `explorer.unresolved`, `explorer.excluded_common` | 분석 범위 밖·공통 노드 → `unverified_segments` |
| `retriever.evidence[]` (`content` 원문) | hop 근거(`evidence_ref`)·확신도, LLM 판단 근거 |
| `retriever.nodes_without_evidence` | 해당 hop 은 `inferred` |
| `replan_feedback` | 재계획 라운드면 이전 누락 사유 (같은 누락 반복 방지) |

**처리**
1. **경로 골격 (규칙, `rules.build_impact_paths`)**: `explorer.paths` 마다 `ImpactPath`.
   - hop: `node` = 경로 노드 ID, `relation` = 앞 노드 → 이 노드 엣지 유형 (첫 hop 은 `ENTRY`), `evidence_ref` = 노드 Evidence 의 `file_path#location` (예: `program/ProdScrbSvc.pgm#L40-88`)
   - `confidence`: Evidence 가 있으면 `confirmed`, 없으면 `inferred`
   - 같은 노드 순서의 경로는 하나로 합친다. 경로 id `P<item_id>-<n>`
   - `cross_domain`: 경로 노드의 `domain` 이 둘 이상 (swing ↔ tos, `rules.SM_PART_DOMAINS`)
   - DB hop 의 컬럼 단위 정보 (예정): DataAccess → Table hop 은 엣지 `columns` 를 LLM 입력에 넣는다. 요건 컬럼이 `WRITES.columns` 에 있으면 변경 후보, `READS.columns` 에만 있으면 조회 영향, 둘 다 없으면 "컬럼 미사용" (PL/SQL DBIO 하나가 여러 테이블을 다루기 때문)
2. **영역 배정 (규칙, `rules.path_areas`)**: 1-1 영역 분류 규칙.
3. **영향 판정 (LLM)**: 영역별로 경로·원문·요건 항목을 주고 `SimulatorDraft` 를 받는다.
   - hop 별 `impact` (이 노드에서 무엇이 바뀌는지 / 확인만 필요한지)
   - 영역별 `impact_type`(add / modify / reference_only / none), `feasibility`, `difficulty` + `difficulty_basis`, `schedule_factors`
   - `feasibility=hard` 면 `infeasible_reason` 과 `alternatives`(가능성·난이도·장단점) 필수
   - 요청안과 대안이 갈리면 `requested_vs_alternative` (예: SC-001 신규 컬럼 추가 vs 기존 광고여부 변환)
   - 요건과 무관한 경로는 **제외 제안** + 사유 (4단계 가드 통과 시에만 제외)
   - 신규 요소 (예정): 그래프에 없는 컬럼·DBIO·배치·코드 데이터가 필요하면 `new_elements`(`suggested_id`·`area`·`reason`). 정답셋 `expected.new` 와 같은 형식이며, 있는 영역은 `impact_type=add` 가능
   - 노드 설명은 `desc` → 없으면 `program_desc`·이름 → 그래도 없으면 `code` 앞부분 (swing 정의서 채움률이 낮음, 6-2)
4. **판정 보정 (규칙)**: LLM 결과를 그대로 믿지 않는다.
   - 그래프에 없는 노드를 hop 에 넣으면 버린다 (경로는 골격 노드만). `new_elements.suggested_id` 가 이미 그래프에 있으면 신규가 아니므로 버린다 (예정)
   - 영역 hop 이 전부 `inferred` 이면 `feasible` 로 확정하지 않고 `review`
   - 영역 `confidence` = hop 이 모두 `confirmed` 일 때만 `confirmed`
   - **경로 제외 가드**: 하나라도 해당하면 제외 제안을 거절하고 경로를 남긴다.
     - 목표 역할에 도달한 경로(`stop_reason=target_reached`)의 마지막 노드에 근거(`confirmed`)가 있음
     - 사용자가 확정한 노드(`Mention.confirmed_node`)가 경로에 있음
     - 그 경로를 빼면 요건 항목(`item_id`)의 경로가 하나도 남지 않음
   - 제외한 경로는 `dropped_paths` 에 사유와 함께 남긴다 → Composer 레포트 "검토 제외 경로"
5. **분석 한계 (`unverified_segments`)**: `explorer.unresolved`, `item_results` 의 미도달·frontier·매칭 실패, 근거 없는 노드, 공통 제외 노드, 고립 노드로 끝난 경로(6-3), swing 컬럼 변경 시 참조 테이블 미확인 (swing 에 `REFERENCES` 없음).

경로 제외를 허용하는 이유: Explorer 경로에는 요건과 무관한 분기가 섞인다 (노드 정밀도 0.2 수준). 현재 그래프에서는 swing 배치 기동기 경로(`DBIO:BatJobSql.*`)와 공통 코드 조회가 특히 많이 섞인다. 대신 KPI 최우선 목표가 Critical 미탐 0건이므로 가드로 미탐 위험을 막고, 제외 내역은 사람이 볼 수 있게 남긴다.

### 1-1. 영역(Area) 분류 규칙

Simulator·Verifier·Composer 가 같은 함수(`rules.path_areas`)를 쓴다.

| 역할 (`NodeRole`) | tos / swing 라벨 | 영역 |
|---|---|---|
| Entry | Screen (팝업·출력 화면 포함) | screen |
| Entry | Batch | batch |
| EntryHandler, Logic | Controller·Service / TP·Module | 경로 진입 노드(Entry)의 영역 |
| DataAccess, Table, Column | SQL·DBIO / DBIO, Table, Column | db |
| (속성) | `properties.interfaces` 가 있거나 근거에 `source_kind=interface_spec` 이 있는 노드 | interface (위 영역에 추가) |
| (그래프 밖) | Explorer `unresolved` 의 그래프 밖 채널·전문 (`ExploreItem.out_of_graph`) | interface, 경로 없이 `unverified_segments` 로 |

- 경로 하나가 여러 영역에 걸칠 수 있다 (Screen → TP → Module → DBIO → Table = screen + db).
- 4개 영역을 항상 모두 출력한다. 경로가 없는 영역은 `impact_type=none` (Verifier 영역 누락 점검 기준).

| 현재 그래프의 경로 모양 | 예 (정답셋) | 영역 |
|---|---|---|
| Screen → Screen(`OPENS`) → Controller → … | SC-004 `SCR:TOSCP0300M → SCR:TOSCP0301P → CTL:…` | screen (+ db) |
| Batch → TP (화면 서비스 재사용) | H-1 `BAT:ZDOCBB0100040 → TP:CombScrbSvc.joinComb` | batch. 같은 TP 가 화면 경로에도 있으면 두 영역 모두 |
| TP → TP (`CALLS`) | H-2 `TP:ProdScrbSvc.getFeeChgInfo → TP:ProdInfoSvc.getSaleProdList` | 진입 영역 |
| Screen 단독 (1노드) | L-2, L-4, SC-005 | screen |
| Batch 단독 (나가는 엣지 없음) | SC-006 참고 `BAT:TBCH1020`·`TBCH4020` | batch (+ interface) |
| EntryHandler 에서 시작 | SC-003 `CTL:CmmCdController.selectCdList → SQL:… → TBL:TCP_CMM_CD` | screen 으로 본다 |
| Table 에서 시작 | SC-002·SC-006 `TBL:… → COL:…` | db 만 |

### 1-2. 난이도 판단 기준

변경(`impact_type` = add / modify)이 닿는 노드의 그래프 단계로 영역 난이도를 정하고, 영역 수·도메인 연계는 요건 전체 난이도에만 반영한다 (`simulator.rule_difficulty`, `requirement_difficulty`).

| 영역 난이도 | 기준 (하나라도 해당) | 판단 근거 |
|---|---|---|
| Major | 컬럼 길이·타입 변경 · 신규 테이블 · 인터페이스 전문 레이아웃 변경 · `REFERENCES` 로 연결된 컬럼 변경 | Column `data_type`, `REFERENCES.via`, interface `modify` |
| Medium | 기존 테이블에 컬럼 추가 · 업무 로직 변경이 2개 이상 노드 · 화면 항목 추가 + 저장 로직 변경 · 신규 DBIO | `new_elements` 의 `COL:`·`DBIO:`, Logic·DataAccess 다수 |
| Minor | 데이터·설정값·문구·이미지 변경 · 공통코드 값 추가 · 배치 주기(`cron`) 변경 · 단일 노드 변경 | Screen `images`, Column `code_values`, Batch `cron`, Table 데이터만 |

- 요건 전체 난이도 (`simulator.requirement_difficulty(area_impacts)`, 출력 필드가 아니라 Composer 요약·정답셋 비교 때 계산): 영역 난이도 중 최고. 단 변경 영역 3개 이상 · 도메인 간 연계 → Major, Medium 이상 영역이 2개 → Medium 이상 (Minor 영역끼리는 개수로 올리지 않음).
- 일정 영향 요인: 영역당 3개 이내 (Simulator), 레포트 전체 8개 + "외 n건" (Composer).

정답셋 대조 (정답 `expected.areas`·`new` 에 기준표를 손으로 적용, 실행 결과 아님). 이전 기준(컬럼 추가 = Major, 변경 영역 2개 = Medium)에서 어긋난 5건:

| 케이스 | 정답 | 이전 기준 | 현재 기준 | 비고 |
|---|---|---|---|---|
| L-1 배치 주기 | Minor | Medium | Minor | db·batch 모두 설정 데이터 변경 |
| L-3 그리드 항목 추가 | Minor | Medium | Minor | 기존 컬럼 조회 추가, 영역마다 단일 노드 |
| M-3 판촉품 지급 체크 | Medium | Major | Medium | 기존 테이블 nullable 컬럼 추가 |
| M-4 결합상품 개선 | Medium | Major | Medium | 같음 |
| SC-002 권한 만료 장애 | Major | Medium | Medium | **미해결** (9장) |

나머지 11건은 두 기준 모두 일치한다.

**출력 `SimulationOutput`**: `area_impacts[AreaImpact{area, impact_type, paths[ImpactPath], feasibility, difficulty, difficulty_basis, schedule_factors, infeasible_reason, alternatives, confidence, new_elements(예정)}]`, `requested_vs_alternative`, `unverified_segments`, `dropped_paths`

**한도·예외**: LLM 없음 → 3단계를 건너뛰고 `impact` = "영향 검토 필요", `impact_type` = 경로 있음 `modify` / 없음 `none`, `feasibility=review`, `difficulty` = 규칙 판정, `new_elements` 비움, 경로 제외 안 함. LLM 입력은 경로 `MAX_CONTEXT_PATHS`·원문 `MAX_CONTEXT_CHARS` 까지 (swing Batch `code` 는 Job 정의 + 실행 함수라 길다). KPI 평가 노드 = `area_impacts[].paths[].hops[].node` (`dropped_paths`·`new_elements` 제외), 재계획이 있었으면 마지막 라운드.

---

## 2. Verifier (2-4)

**역할**: Simulator 결과의 영역 누락과 근거를 검증하고, 보완이 필요하면 재계획을 요청한다 (다이어그램의 빨간 점선 → Supervisor).

**입력**: `simulation`, `planner.items`, `explorer`(경로·`item_results`·`unresolved`), `retriever.evidence`, `replan_feedback`

**처리**
1. 점검 (`VerifyCheck.check`)

| check | 방식 | 보완 필요 조건 |
|---|---|---|
| `area_path_missing` | 규칙 | 변경 유형이 가리키는 필수 영역(아래 표)에 경로가 없음 / 항목의 경로가 하나도 없음 (`reached=false` 이고 경로 0) |
| `evidence_missing` | 규칙 | `confirmed` hop 의 `evidence_ref` 가 Retriever 근거에 없음 / `modify`·`add` 영역의 hop 이 전부 `inferred` (`add` 영역이 `new_elements` 만이면 제외, 예정) |
| `cross_domain_link` | 규칙 | 경로 노드의 domain 이 섞였는데 `cross_domain=false` |
| `redundant_path` | 규칙 | 같은 노드 순서 중복, 한 경로가 다른 경로에 완전히 포함 (경고만, 재계획 사유 아님) |
| `alternative_basis` | 규칙 + LLM | `hard` 영역에 대안 없음 / 대안 설명이 근거(Evidence·노드)를 가리키지 않음 |

2. 필수 영역 (`rules.CHANGE_TYPE_RULES` 목표 역할과 맞춤)

| change_type | 목표 역할 | 필수 영역 |
|---|---|---|
| `screen_change` | EntryHandler | screen (Screen 단독 경로로 충족, L-2·L-4·SC-005) |
| `query_field_add` | DataAccess | screen, db |
| `data_add` | Table | db |
| `column_spec_change` | Entry (reverse) | db |
| `batch_change` | DataAccess | batch |
| `interface_field_add` | Entry (reverse) | interface (그래프 밖이면 `unresolved` 표시로 충족) |
| `logic_change`, `ops_cause`, `structure_query` | Logic / Table / Column | 점검 안 함 (SC-002·SC-006 처럼 Table 에서 시작하는 경로가 정상) |

3. 판정
   - 하나라도 보완 필요 → `result=needs_supplement`, `reason`(무엇이 왜 빠졌는지), `additional_scope`
   - `additional_scope` 는 Planner 가 탐색 명세로 바꿀 수 있는 형태: `node:<노드ID>` / `area:<영역>` / `keyword:<표현>`. Planner 는 `[이전 탐색 명세]` + `[재계획 요청]` 블록으로 받는다
   - 같은 사유가 이전 `replan_feedback` 에 이미 있으면 재계획해도 해결되지 않는 것으로 보고 `pass`, 사유는 `checks[].note` (Composer 가 분석 한계로 표시)
   - 고립 노드(6-3)로 끝나 경로가 이어지지 않는 영역은 `additional_scope` 에 넣지 않고 분석 한계로
   - 경고 (예정): 화면 경로의 TP 가 Batch→TP 로 배치에서도 호출되는데 batch 영역이 `none` 이면 `checks[].note` (H-1 `TP:CombScrbSvc.joinComb`)

**출력 `VerifierOutput`**: `result`(pass / needs_supplement), `checks[VerifyCheck]`, `reason`, `additional_scope`

**한도·예외**: 재계획은 Supervisor 가 `MAX_REPLAN`(2) 까지. 넘으면 그대로 Composer 로 가고 미해결 점검은 Composer 분석 한계로. 점검 하나당 `additional_scope` 최대 `MAX_SCOPE` 개. LLM 없음 → `alternative_basis` 의 근거 판단만 건너뛴다 (대안 유무만 규칙 점검).

---

## 3. Composer (3)

**역할**: 분석 결과를 사용자 결과물로 만든다. 단순 → 단순 요건 응답, 복잡 → 레포트·체크리스트, 요청 시 회신 메일 초안·MD/PDF.

**입력**

| flow | 사용 |
|---|---|
| 단순 (Retriever → Composer) | `planner.structured_request`, `planner.items`, `explorer.paths`, `retriever.evidence`, `retriever.nodes_without_evidence`, `explorer.unresolved` |
| 복잡 (Verifier → Composer) | 위 + `simulation`, `verifier` (미해결 점검) |
| 공통 | `user_input.output_options`, `followups` (후속 답변), 이전 `composer` (재작성 시) |

### 3-1. 단순 요건 응답 (`SimpleAnswer`)

다이어그램: **요약 결론 · 영향 경로 · 근거**

| 필드 | 작성 |
|---|---|
| `conclusion_lines` (3~5줄) | LLM. 결론 먼저, 노드명·테이블명을 붙여 쓴다 |
| `impact_paths` | 코드. `explorer.paths` → `ImpactPath` (Simulator 1단계와 같은 함수, `impact` 는 LLM 한 줄) |
| `evidence_table` | 코드. Evidence 마다 `claim`(LLM 요약 또는 노드 설명) · `evidence` · `location` · `confidence` |
| `check_queries` | LLM. 운영 문의일 때 확인용 SQL. **코드에서 SELECT 단일 문장만 통과**. 코드값 해석은 Column `code_values`(swing) / `TCP_CMM_CD` 조회(tos) (SC-006) |
| `analysis_limits` | 코드. `explorer.unresolved`, `nodes_without_evidence` |
| `detail_analysis_recommended` | 규칙 + LLM. 변경 유형이 add/modify 이거나 LLM 이 변경 요소를 찾으면 `true` |

### 3-2. 복잡 요건 결과물 (`ComplexReport`)

다이어그램: **레포트(영역별 영향·가능성·대안) · 체크리스트(업무·개발 확인 항목) · 회신 메일·PDF(요청 시)**

| 필드 | 작성 |
|---|---|
| `summary_conclusion` | LLM. 가능 여부·난이도·핵심 영향 3~5문장 |
| `area_verdicts` | 코드. `simulation.area_impacts` 를 영역별 한 행 (경로 요약 "진입 → … → 끝 노드", 최대 `MAX_PATH_SUMMARY` 개) |
| `new_elements` (예정) | 코드. 영역별 `new_elements` → 레포트 "신규 개발 요소" 절 + 개발 체크리스트 |
| `schedule_factors` | 코드. 영역별 요인 합치기 (중복 제거) |
| `path_details` | 코드. `simulation` 경로 그대로 (같은 경로는 한 번만) |
| `analysis_limits` | 코드. `unverified_segments` + Verifier 반복·경고 점검 + 재계획 한도 초과 사실 |
| `dropped_paths` | 코드. "검토 제외 경로" 절 + 개발 체크리스트 |
| `checklist` | LLM 초안 + 코드 정리 (아래) |
| `stage` | `initial` → (후속 답변 반영) `updated` → (체크리스트 전부 확정) `final_for_dev` |

체크리스트 규칙
- `owner=business`: 정책·업무 결정 질문 (예: 상품코드 분리 여부, 할인 정책). `owner=developer`: 구현 방식·테스트 범위 확인 (예: 하드코딩 위치, 함께 영향받는 배치).
- 출처: `structured_request.open_items`, `inferred` hop, `review` 영역, 대안 선택 지점, 검토 제외 경로, 신규 요소, 공통 노드 영향(`excluded_common`), 화면·배치 공유 TP.
- `item_id` 는 `B-1, B-2… / D-1, D-2…`. 후속 답변(`FollowupInput.target_item_ids`)은 이 id 로 연결.

### 3-3. 후속 입력과 재분석

Supervisor 의 `Supervisor.followup(session_id, FollowupInput)` 이 진입점을 고른다: `business_answer`·`developer_opinion`·`query_result` → Composer (결과물만 갱신) / `change_analysis`·`reanalysis` → Planner (전체 flow).

1. `target_item_ids` 의 체크리스트 항목에 `answer` 를 채우고 `status=confirmed`, `stage` 갱신.
2. `query_result` 는 `query_result_summary`(코드 의미·주요 원인·문의 항목)로 정리.
3. 답변 때문에 영향 경로 자체가 바뀌어야 하면 (예: "B 를 별도 상품코드로 관리") `reanalysis_request`(사유·바뀐 가정)를 반환한다. 자동 재분석은 하지 않는다 — UI 가 보여주고 사용자가 확인하면 `FollowupInput(type=reanalysis)` 로 기존 Planner 진입 흐름을 탄다 (Supervisor 변경 없음).

### 3-4. 추가 산출물 (`UserInput.output_options`)

- `mail_draft`: 회신 메일 초안 (`subject`, `body`). 결론 → 영향 영역 → 확인 요청(business 체크리스트) 순서. 수신자는 비워 둔다.
- `pdf`: `save_report(format="pdf")` → `report_file_path` (`markdown` + `weasyprint`, `report` 옵션 묶음).
- `markdown`: 항상 `templates/simple_answer.md.j2` / `complex_report.md.j2` 로 렌더링. LLM 이 markdown 을 직접 쓰지 않는다. "검토 제외 경로"·"신규 개발 요소"(예정) 절 포함.

**출력 `ComposerOutput`**: `complexity`, `simple_answer` 또는 `complex_report`, `markdown`(템플릿 렌더링 결과), `mail_draft`, `report_file_path`, `reanalysis_request`

**한도·예외**: LLM 없음 → 결론·요약은 규칙 문구(영역·경로 수·판정 나열), 체크리스트는 open_items 와 `review` 영역에서만. LLM 입력 근거는 `MAX_CONTEXT_EVIDENCE` 개까지. SELECT 외 쿼리는 버린다.

---

## 4. KPI Agent

**역할**: Composer 이후 PoC 평가. 정답 노드 목록과 실행 로그를 대조해 노드 판정(탐지·미탐·오탐·제외·범위 밖)과 탐지율·미탐률·Critical 미탐률·정밀도, 실행 성능을 낸다 (LLM 미사용). 상세는 [SE_kpi-agent-design.md](SE_kpi-agent-design.md).

**출력 `KpiOutput`**: `metrics`, `judgements`, `by_type`, `by_stage`, `missed_nodes`, `false_positive_nodes`, `all_passed`, `warnings` (Composer 에 전달하지 않음)

---

## 5. 파트너 담당 (인터페이스만)

| 에이전트 | 이 설계가 받는 것 | 이 설계가 돌려주는 것 |
|---|---|---|
| Supervisor | 라우팅: Retriever → Simulator(복잡) → Verifier → Composer → KPI, 단순은 Retriever → Composer. `analyze(..., until="simulator" \| "verifier")` 단계 실행 | `needs_supplement` 시 `replan_feedback` 누적 (`_verifier_node`, `_route_verifier`). 각 에이전트 `last_usage` → `AgentRunRecord` |
| Planner | `items[ExploreItem]`(`change_type`·`mentions`·`out_of_graph`), `structured_request.open_items`, `rules.CHANGE_TYPE_RULES`·`SM_PART_DOMAINS` | Verifier `reason`·`additional_scope` (`node:`·`area:`·`keyword:`) → 재계획 입력 |
| Explorer | `paths`(진입 → DB), `nodes`, `edges`(속성 포함), `item_results`, `unresolved`, `excluded_common` | - |
| Retriever | `Evidence { node_id, file_path, location, source_kind, content }`, `nodes_without_evidence` | - (`evidence_ref` = `file_path#location`) |
| UI | - | `ComposerOutput` 렌더링, `reanalysis_request` 표시 → 사용자 확인 시 `reanalysis` 후속 입력 |

- 재계획 라운드에서 `simulation`·`verifier` 는 다음 라운드 결과로 덮어쓴다 (Explorer·Retriever 도 새로 실행).
- 한 요청은 한 시스템만 탐색한다 (SM 선택 → `SM_PART_DOMAINS`). 그래서 지금은 `cross_domain=true` 경로가 생기지 않는다 (그래프에도 swing ↔ tos 엣지 0건). `cross_domain_link` 점검과 Major 기준의 도메인 연계 항목은 동작하지만 쓰일 일이 없다.

---

## 6. 데이터 (공통 기반, 읽기 전용)

공용 데이터는 팀이 함께 운영하므로 **이 설계에서 바꾸지 않는다**. 아래는 2026-10-27 Neo4j 적재 결과를 조회한 값이며, 공용 데이터가 갱신되면 이 장부터 다시 맞춘다. 노드 ID 규칙·엣지 근거는 [mock_data.md](mock_data.md), 그래프 그림은 [impact_graph_structure.svg](impact_graph_structure.svg).

| 대상 | 원칙 |
|---|---|
| `data/source`, `data/docs`, `data/graph`, Neo4j | 읽기만. 설명 누락·`REFERENCES` 없음·적재 범위 밖 소스는 에이전트 규칙·분석 한계로 처리 |
| `eval/ground_truth` | 읽기만. 정답셋에 없는 사례(예: `hard`·대안)는 `tests/` 의 가상 노드 fixture 로 검증 |

### 6-1. 노드·엣지 (노드 688 · 관계 850)

| 단계 | 역할 | swing (366) | tos (322) |
|---|---|---|---|
| 0 진입 | Entry | Screen 13, Batch 10 | Screen 8, Batch 7 |
| 1 처리 진입 | EntryHandler | TP 34 | Controller 13 |
| 2 업무 로직 | Logic | Module 24 | Service 23 |
| 3 데이터 접근 | DataAccess | DBIO 60 (PL/SQL 패키지 함수) | SQL 42 (XML · Pro*C), DBIO 4 (배치 직접 호출) |
| 4 / 5 | Table / Column | 23 / 202 | 22 / 203 |

| 엣지 | swing (491) | tos (359) |
|---|---|---|
| CALLS | Screen→TP 40, TP→TP 18 | Screen→Controller 12 |
| OPENS | Screen→Screen 3 | Screen→Screen 6 |
| INVOKES | TP→DBIO 45, TP→Module 30, Batch→DBIO 27, Module→DBIO 23, Module→Module 12, Batch→Module 12, Batch→TP 2 | Controller→Service 13, Service→Service 9, Batch→DBIO 4 |
| EXECUTES | — | Service→SQL 36, Batch→SQL 11, Controller→SQL 1 |
| READS / WRITES | DBIO→Table 49 / 28 | SQL→Table 24 / 25, DBIO→Table 10 / 3 |
| REFERENCES | — (DDL 에 FK 없음) | Table→Table 2 (`via=CMPN_ID`) |
| HAS_COLUMN | 202 | 203 |

### 6-2. 이 설계가 쓰는 속성

| 속성 | 있는 노드·엣지 | 사용처 |
|---|---|---|
| `code` | Column 제외 전부 (Batch 는 Job 정의 + 실행 함수 원문) | LLM 판단 원문 (근거가 없을 때 보조) |
| `desc`, `program_name`, `program_desc` | swing 은 정의서에서 채움, 채움률 낮음 (DBIO 15/60, TP 22/34, Module 18/24) | 노드 설명. 비면 이름 + `code` 앞부분 |
| `interfaces` (`"<IF_ID> <이름>"`) | swing TP 5 (`IF_PAY_0010/0020/0030`), swing Batch 2 (`IF_SMS_0010`, `IF_CDTS_0010`), tos Batch 4 (`IF-TOS-MMS-011/012`, `IF-TOS-EXT-041`, `IF-TOS-TWD-021`) | interface 영역 |
| `images` | swing Screen 2 (`ZROMRR0700090`, `ZROMRR0700340`) | 이미지·문구 교체 → Minor (L-2) |
| `cron`, `cycle` (tos `steps`·`group`) | Batch 전부 | 배치 주기 변경 (L-1, swing 실제 주기는 `TB_BAT_JOB.CRON_EXPR`) |
| `code_grp`, `code_values` | swing Column 20 (tos 는 `TCP_CMM_CD` 테이블 데이터) | 코드값 추가 = 데이터 변경 |
| `data_type`, `nullable`, `pk` | Column 전부 | 컬럼 길이·타입 변경 |
| `READS.columns`, `WRITES.columns` | DataAccess → Table 엣지 | 컬럼 단위 영향 |
| `url`·`http_method`, `stmt_type`, `dbio_type`·`used_by` | tos Controller, SQL, DBIO | hop 영향 설명 보조 |

### 6-3. 특이 노드

| 구분 | 노드 | 처리 |
|---|---|---|
| 공통 (`is_common`, 11개) | swing `MOD:IfClient.send`, `MOD:BatchLauncher.runJob`, `MOD:AgeCalcUtil.*` 3, `MOD:CommCdSvc.getCdNm/getCdAttr`, `TP:CommCdSvc.getCdList`, `DBIO:CommCdSql.selectCdList` / tos `CTL:CmmCdController.selectCdList`, `SQL:CmmCdMapper.selectCdList` | Explorer 가 확장하지 않음 (`excluded_common`). 정답셋은 일부를 `reference_only`(H-2·L-1) 또는 경로 시작(SC-003)으로 둔다 → `unverified_segments` + 개발 체크리스트 "공통 모듈 영향 확인" |
| 배치 기동기 공통 경로 | swing `LAUNCHER` 기동 Job → `DBIO:BatJobSql.updateLastRun`·`insertRunHst` | 요건과 무관한 분기로 자주 나옴 → 경로 제외 후보 1순위. 단 L-1(배치 주기)은 이 경로가 정답 |
| 고립 노드 | `CTL:LoginController.logout`, `SVC:CampMsgService.appendRejectFooter`, `BAT:TBCH1020`, `BAT:TBCH4020` | 1노드 경로. `BAT:TBCH1020/4020` 은 인터페이스만 있고 처리 소스가 적재 범위 밖 → 분석 한계 |
| 한 함수에 SQL 여러 개 | tos `SQL:<프로그램>.<함수>#<n>` 3개 (Pro*C) | ID 그대로. 경로 요약에서 `#n` 을 지우지 않는다 |
| 화면·배치 공유 TP | Batch→TP 2건 (예: `TP:CombScrbSvc.joinComb`) | TP 변경은 screen·batch 두 영역 영향 → 체크리스트 |

## 7. 스키마 변경 요약

| 모델 | 변경 |
|---|---|
| `VerifyCheck.check` (`CheckKind`) | + `evidence_missing` |
| `SimulationOutput` | + `dropped_paths[DroppedPath{path, reason}]` |
| `ComplexReport` | + `dropped_paths` |
| `PathHop.evidence_ref` | 설명 `file_path#location` (Retriever Evidence 와 맞춤) |
| LLM 응답 (`schemas.json` 비공개) | `SimulatorDraft`, `VerifierDraft`, `SimpleDraft`·`ComplexDraft`·`FollowupDraft` 신규 |
| `AreaImpact`, `ComplexReport`, `SimulatorDraft` (예정) | + `new_elements[NewElement{suggested_id, area, reason}]` (정답셋 `expected.new` 형식) |

`python scripts/export_schema.py --write` 로 `schemas.json` 갱신, `tests/test_schema_sync.py` 가 일치 검사. 출력 모델이 바뀌면 프론트 목업(`frontend/src/mocks/fixtures/scenarios.ts`)도 맞춘다.

## 8. 실행·검증

```bash
python -m impact_agent --case SC-001 --until simulator                     # Simulator 까지
python -m impact_agent --case SC-001 --until verifier                      # Verifier 까지
python -m impact_agent --case L-1 --answer-key eval/ground_truth/L-1.json --out L-1.state.json   # Composer → KPI
python scripts/eval_kpi.py outputs/runs/<run_id>                           # 저장된 state 일괄 KPI
pytest tests/test_simulator.py tests/test_verifier.py tests/test_composer.py tests/test_templates.py   # 가짜 LLM·그래프
```

pytest (LLM·Neo4j 불필요, 가짜 LLM `Fixed` 또는 LLM 없음, tos·swing 노드 케이스 각각):

| 대상 | 확인 |
|---|---|
| Simulator | 경로 골격(노드 순서·엣지 유형·evidence_ref·확신도), 역할 기준 영역 배정, 경로 모양(`OPENS`·Batch→TP·Screen 단독·Table 시작·고립 노드), 그래프 밖 노드 제거, inferred 영역 review, 경로 제외 가드 3종, 난이도 기준, LLM 없음 출력 |
| Verifier | 점검 항목별 보완 필요/통과, 필수 영역 표, `additional_scope` 형식, 같은 사유 반복 시 pass, 재계획 한도 (`test_supervisor`) |
| Composer | 단순/복잡 필드 조립, SELECT 외 쿼리 차단, 체크리스트 id·후속 답변·stage 전이, `reanalysis_request`, 검토 제외 경로, markdown 렌더링 |
| 전체 | `Supervisor.run` 이 Composer·KPI 까지 끝나고, 복잡 flow 도 KPI `evaluable=true` |

정답셋 대조 (`eval/ground_truth`, 16건, 읽기 전용):

| 구분 | swing (상품 SM, 10건) | tos (마케팅 SM, 6건) |
|---|---|---|
| 난이도 | Major 2 (H-1·2), Medium 4 (M-1~4), Minor 4 (L-1~4) | Major 2 (SC-001·002), Medium 2 (SC-003·004), Minor 2 (SC-005·006) |
| interface | M-2 `reference_only` | SC-001 `modify` (유일한 interface 변경) |
| `expected.new` | H-1, H-2, M-3, M-4 | SC-002, SC-003 |
| feasibility | 전부 feasible | 전부 feasible |

| 정답 필드 | 비교 대상 |
|---|---|
| `expected.areas` | Simulator `area_impacts[].impact_type` |
| `expected.feasibility` | Simulator 영역 판정 |
| `difficulty` | `requirement_difficulty(area_impacts)` (1-2 대조표) |
| `expected.new` | `new_elements` (ID 완전 일치가 아니라 영역 + 대상 테이블/프로그램 일치. 정답 ID 가 예시 수준인 경우가 있음) |
| `expected.reference_only` | `reference_only` hop / 공통 노드 체크리스트 |
| `conclusion.correct / wrong`, `wrong_paths` | `requested_vs_alternative`, 오답 경로 채택 여부 |
| `expected.open_items` | Composer 체크리스트 |
| `analysis_limits` | Composer 분석 한계 |

## 9. 남은 과제

1. `new_elements` 반영: 스키마(`NewElement`) → Simulator 보정 → Verifier `evidence_missing` 예외 → Composer "신규 개발 요소" 절·체크리스트 → 프론트 목업
2. Simulator: 난이도 규칙 조정(1-2) 코드 반영, DB hop 엣지 `columns`·노드 속성(`images`·`cron`·`code_values`·`data_type`)을 LLM 입력에, 고립 노드·swing REFERENCES 분석 한계
3. Verifier: 화면·배치 공유 TP 경고, 고립 노드 재계획 제외
4. 난이도 기준 검증: 조정안은 손 대조 결과라 16건 실행 후 재확인. **SC-002(운영 장애, 정답 Major)는 기준표로 설명이 안 됨** → 운영 장애·권한 요건 가중 여부 결정
5. 공통 노드 처리: 정답셋이 공통 노드를 `reference_only`·경로 시작으로 쓰는 경우(H-2·L-1·SC-003)와 Explorer 공통 제외 규칙을 맞출지 (파트너 합의)
6. 자동 재분석: 지금은 사용자 확인 후 `reanalysis` 후속 입력. 자동화가 필요하면 Supervisor 에 `reanalysis_request` 라우팅 추가 (파트너 합의)
7. 두 시스템에 걸친 요건: Planner 탐색 영역이 SM 선택 하나로 고정. 필요하면 `SM_PART_DOMAINS` 확장 (파트너 합의)
8. `hard`·대안 사례 검증: 정답셋 16건 모두 feasible. 정답셋은 그대로 두고 `tests/` 가상 노드 fixture 로 검증
9. swing 정의서 채움률: 데이터는 그대로 두고 LLM 이 `code` 원문으로 판단. 설명 없는 노드가 판정 품질에 주는 영향을 실행 결과로 확인
10. LLM 입력 길이 상한 (노드당 원문, 전체 상한, 요약 여부), PDF 변환(weasyprint 한글 폰트·사내 설치 가능 여부), 확인 쿼리 DB 문법 차이 (swing PL/SQL Oracle, tos MyBatis·Pro*C)
11. `new_elements` KPI 일치 기준을 KPI 설계서에 반영할지
12. 검증: `run_scenarios.py` 를 Composer 까지 돌려 `state.json` 저장 → `eval_kpi.py` 로 KPI, 1-2 난이도 대조표를 실행 결과로 갱신
