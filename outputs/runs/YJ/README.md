# YJ 에이전트 실행 기록 (Supervisor · Planner · Explorer · Retriever)

`scripts/YJ/run_scenarios.py`, `scripts/YJ/run_guardrail.py` 실행 결과. 그래프는 Neo4j 적재본 기준.
케이스별 `<케이스>.md` 는 단계별 기록(Planner → Explorer → Retriever, ReAct Thought·Action·Observation, 근거 목록, 정답 대비 평가)이고,
`summary.md` / `summary.json` 은 케이스별 지표(`ReAct / 규칙만`)입니다. `<케이스>.state.json` 은 소스 원문을 담고 있어 저장소에 올리지 않습니다.

구조: `<에이전트 버전>/<영역>` (tos = 마케팅, swing = 상품) + `<에이전트 버전>/guardrail/r1~r3`

| 폴더 | 실행일 | 케이스 |
|---|---|---|
| `v1/tos/` | 2026-10-26 | SC-001 · SC-003 · SC-004 · SC-005 · SC-006 (5건) |
| `v1/swing/` | 2026-10-26 | H-1 · M-2 · M-4 · L-1 · L-3 (5건) |
| `v2/tos/` | 2026-10-26 (SC-002 는 2026-10-27) | SC-001 ~ SC-006 (6건) |
| `v2/swing/` | 2026-10-26 (H-2·M-1·M-3·L-2·L-4 는 2026-10-27) | H-1 · H-2 · M-1 ~ M-4 · L-1 ~ L-4 (10건) |
| `v2/guardrail/r1~r3/` | 2026-10-26 | 가드레일 10종 × 3회 |
| `v3/tos/` | 2026-10-27 | SC-001 ~ SC-006 (6건) |
| `v3/swing/` | 2026-10-27 | H-1 · H-2 · M-1 ~ M-4 · L-1 ~ L-4 (10건) |
| `v3/guardrail/r1~r3/` | 2026-10-27 | 가드레일 10종 × 3회 |

- v1 = 설계 기술(페르소나·Few-shot·ReAct·Reflexion·LangGraph) 반영, v2 = v1 테스트 결과로 개선 (Explorer 점검 항목, Planner 판정 기준, Retriever 입력 축소)
- v3 = v2 테스트 결과로 개선 (Supervisor 조기 종료·tool 한도 배분, Explorer 점검 신호·검색 중복 제거, Retriever 근거 누적·이어 읽기·컬럼 되돌리기, Planner 운영 문의 기준)
- 튜닝 6건: SC-001·SC-003·SC-006·H-1·M-2·L-3 / 홀드아웃 4건: SC-004·SC-005·M-4·L-1 / v3 추가 6건: SC-002·H-2·M-1·M-3·L-2·L-4 (v2 에는 v3 비교 기준으로 2026-10-27 에 v2 코드로 실행해 넣음)
- 재실행 예: `PYTHONPATH=src python3 scripts/YJ/run_scenarios.py SC-001 … SC-006 --run-id v3/tos`, `PYTHONPATH=src python3 scripts/YJ/run_guardrail.py --run-id v3/guardrail/r1`
