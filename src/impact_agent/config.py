"""런타임 상수. 값은 schemas/schemas.json 의 `constants` 를 단일 원천으로 읽는다."""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "schemas.json"

_constants: dict[str, float] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["constants"]

MAX_EXPLORE_LOOP: int = _constants["MAX_EXPLORE_LOOP"]  # Explorer↔Retriever 재탐색 최대 횟수
MAX_REPLAN: int = _constants["MAX_REPLAN"]  # Verifier → Planner 재계획 최대 횟수
MAX_TOOL_CALLS: int = _constants["MAX_TOOL_CALLS"]  # 한 요청당 tool 호출 상한
MAX_HOPS_FORWARD: int = _constants["MAX_HOPS_FORWARD"]  # 단방향 탐색 hop 상한 (Screen→TP→Module→DBIO→Table→Column = 5)
MAX_HOPS_BIDIRECTIONAL: int = _constants["MAX_HOPS_BIDIRECTIONAL"]  # forward_then_reverse 탐색 hop 상한
MEMORY_WINDOW_N: int = _constants["MEMORY_WINDOW_N"]  # 후속 대화에서 유지할 최근 턴 수
KPI_MIN_RECALL: float = _constants["KPI_MIN_RECALL"]  # KPI: 재현율 하한 (잠정)
KPI_MIN_PRECISION: float = _constants["KPI_MIN_PRECISION"]  # KPI: 정밀도 하한 (잠정)
