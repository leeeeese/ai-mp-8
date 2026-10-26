# 요건별 정답 (ground truth)

에이전트 출력 평가용 정답. **KPI 는 노드 ID 경로 기준 JSON 을 씁니다** (라인 단위 정답은 KPI 에서 제외).

- `H-*/M-*/L-*.json` (swing), `SC-*.json` (tos): 노드 ID 경로 기준. 아래 형식.
- 소스 위치(file·lines·anchor) 기준 tos 근거(`SC-*.yaml`)와 목업 검증 스크립트는 로컬 작업 폴더 `mock_dev/` 에 있습니다 (git 미포함).

요건 1건당 JSON 1개 (`<요건ID>.json`, 예: `L-1.json` = 난이도 하-1).
**에이전트 입력으로 쓰지 않습니다.** 노드 ID 는 v2 그래프 체계(Screen/TP/Batch/Module/DBIO/Table/Column)를 따릅니다.

| 파일 접두어 | 난이도 | Difficulty | 기본 complexity |
|---|---|---|---|
| `SC-` (tos) | 상·중·하 (파일별) | Major / Medium / Minor | 상·중 complex, 하 simple |
| `H-` | 상 | Major | complex |
| `M-` | 중 | Medium | complex |
| `L-` | 하 | Minor | simple |

## 형식

```jsonc
{
  "req_id": "L-1",
  "title": "구비서류 처리 자동화 배치 주기 단축 요청",
  "difficulty": "Minor",
  "complexity": "simple",
  "sm_part": "product",
  "request_text": "...",                // 에이전트 입력(UserInput.request_text) 그대로 쓸 요청 문장

  "expected": {
    "feasibility": "feasible",          // feasible / hard / review
    "start_keywords": ["구비서류", "배치"],   // Explorer 가 시작 노드를 찾을 때 기대하는 키워드
    "start_nodes": ["BAT:ZDOCBB0100010"],     // 기대 시작 노드 (하나 이상 맞히면 성공)
    "areas": {                          // 영역별 영향 (impact_type: add / modify / reference_only / none)
      "screen": "none", "db": "modify", "interface": "none", "batch": "reference_only"
    },
    "impacted": [                       // 변경이 필요한 기존 노드
      {"node_id": "COL:TB_BAT_JOB.CRON_EXPR", "area": "db", "impact_type": "modify",
       "reason": "CRON_EXPR 30분 → 5분 데이터 변경", "must_find": true},
      {"node_id": "SCR:ZROMRR0700090", "area": "screen", "impact_type": "modify", "reason": "...", "must_find": true,
       "detail": ["images: rom_ib_eqp_k.jpg", "event btnSearch_onClick"],      // (선택) 노드 안의 변경 지점
       "detail_exclude": ["images: rom_ib_sim_k/e/j/c.jpg - ..."]}            // (선택) 같은 노드 안의 비대상
    ],
    "paths": [                          // 정답 영향 경로 (KPI 경로 재현율·Critical 미탐율·정밀도 기준)
      {"path_id": "P1", "nodes": ["BAT:ZDOCBB0100010", "DBIO:BatJobSql.updateLastRun", "TBL:TB_BAT_JOB", "COL:TB_BAT_JOB.CRON_EXPR"],
       "critical": false,              // 놓치면 장애·과금 오류로 이어지는 경로
       "status": "confirmed",          // confirmed / pending(실제 영향 미확정 → KPI 계산 제외, 평가 보류 건수로 공개)
       "note": "..."}
    ],
    "new": [                            // 신규로 만들어야 하는 대상 (아직 소스에 없음)
      {"suggested_id": "...", "area": "screen", "reason": "..."}
    ],
    "reference_only": ["MOD:BatchLauncher.runJob"],   // 영향 경로상 확인만 필요한 노드
    "open_items": [                     // 현업/개발 확인이 필요한 사항 (체크리스트 기대값)
      {"owner": "business", "question": "..."}
    ]
  },

  "distractors": [                      // 키워드가 겹치지만 영향이 없는 방해 소스
    {"node_id": "BAT:ZDOCBB0900010", "why_confusing": "구비서류 이미지 정리 배치. 주기 변경 대상 아님"}
  ]
}
```

- `must_find: true` 인 노드를 놓치면 오답, `false` 는 찾으면 가점.
- 노드 ID 는 `data/graph/nodes.csv` 에 존재해야 합니다 (`new` 제외).
- `paths` 는 진입 노드(Screen/Batch)부터 변경이 필요한 노드까지 **호출되는 쪽(downstream)** 순서로 쓰고,
  연속한 두 노드는 그래프에 `from → to` 엣지로 존재해야 합니다 (`check_ground_truth.py` 가 검사).
- Critical·미확정 여부는 평가 전에 확정합니다 (현재 값은 1차 지정, 검토 필요).
- 화면 이벤트·출력 이미지·인터페이스처럼 노드가 아닌 속성 단위 변경은 해당 노드의 `detail` 로 표현합니다.
- `scripts/check_ground_truth.py` 는 시작 노드에서 2hop(Explorer 기본 탐색 범위)을 넘는 `must_find` 노드를 `[INFO]` 로 알려줍니다.
  현재 상-1(`MOD:CombDcBatch.calcDcAmt`, `TBL:TB_PROD`)·하-1(`COL:TB_BAT_JOB.CRON_EXPR`)이 해당하며, 재탐색이 필요한 의도된 케이스입니다.
- `SC-*.json` 의 추가 키 (KPI 계산에는 쓰지 않음, 평가 리포트·결론 비교용):
  - `domain`, `input_type` (`requirement` / `ops_inquiry`)
  - `impacted[].impact_type = reference_only` + `must_find: true`: 변경은 없지만 결론에 꼭 필요한 근거 노드 (운영 문의의 원인 위치 등)
  - `wrong_paths`: 정답처럼 보이는 다른 경로 (`looks_like`, `why_wrong`)
  - `wrong_impacts`: 경로는 같지만 이 노드를 바꾸면 오답인 경우 (예: 로그인 조건 수정)
  - `conclusion` (`correct` / `wrong`), `answer_order`, `evidence` (노드가 아닌 문서·운영 데이터 근거, 파일 단위), `analysis_limits`
- 경로는 진입 노드가 없는 데이터 조치·구조 질의면 Table 에서 시작할 수 있습니다 (예: SC-002 P4, SC-006).
