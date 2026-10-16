"""런타임 상수. 값은 schemas/schemas.json 의 `constants` 를 단일 원천으로 읽는다."""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "schemas.json"

_constants: dict[str, float] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["constants"]

MAX_EXPLORE_LOOP: int = _constants["MAX_EXPLORE_LOOP"]  # Explorer↔Retriever 재탐색 최대 횟수
MAX_REPLAN: int = _constants["MAX_REPLAN"]  # Verifier → Planner 재계획 최대 횟수
MAX_TOOL_CALLS: int = _constants["MAX_TOOL_CALLS"]  # 한 요청당 tool 호출 상한
MEMORY_WINDOW_N: int = _constants["MEMORY_WINDOW_N"]  # 후속 대화에서 유지할 최근 턴 수
KPI_MIN_EVIDENCE_COVERAGE: float = _constants["KPI_MIN_EVIDENCE_COVERAGE"]  # 근거 확보율 기준
KPI_MIN_CONFIRMED_RATIO: float = _constants["KPI_MIN_CONFIRMED_RATIO"]  # confirmed 판정 비율 기준
