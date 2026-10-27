# YJ 에이전트 실행 기록 (Supervisor · Planner · Explorer · Retriever)

`scripts/YJ/run_scenarios.py`, `scripts/YJ/run_guardrail.py` 실행 결과. 그래프는 Neo4j 적재본 기준.
케이스별 `<케이스>.md` 는 단계별 기록(Planner → Explorer → Retriever, ReAct Thought·Action·Observation, 근거 목록, 정답 대비 평가)이고,
`summary.md` / `summary.json` 은 케이스별 지표(`ReAct / 규칙만`)입니다. `<케이스>.state.json` 은 소스 원문을 담고 있어 저장소에 올리지 않습니다.

| 폴더 | 내용 |
|---|---|
| `20261026-neo4j-v1/` | tos 5건 (SC-001·SC-003·SC-006·SC-004·SC-005), v1 |
| `20261026-neo4j-v2/` | tos 5건, v2 + 가드레일 3회 (`guardrail-r1~r3/`) |
| `20261026-swingcode-v1/` | swing 5건 (H-1·M-2·L-3·M-4·L-1), v1 |
| `20261026-swingcode-v2/` | swing 5건, v2 |

- v1 = 설계 기술(페르소나·Few-shot·ReAct·Reflexion·LangGraph) 반영, v2 = v1 테스트 결과로 개선 (Explorer 점검 항목, Planner 판정 기준, Retriever 입력 축소)
- 튜닝 6건: SC-001·SC-003·SC-006·H-1·M-2·L-3 / 홀드아웃 4건: SC-004·SC-005·M-4·L-1
