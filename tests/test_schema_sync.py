"""schemas/schemas.json 과 Pydantic 모델이 어긋나지 않는지 검사한다."""

import json
from enum import Enum

import pytest
from pydantic import BaseModel

from impact_agent import config, schemas

SPEC = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
DEFS = SPEC["$defs"]


@pytest.mark.parametrize("name", sorted(DEFS))
def test_def_has_model(name):
    assert hasattr(schemas, name), f"$defs.{name} 에 대응하는 모델이 없습니다"


@pytest.mark.parametrize("name", sorted(DEFS))
def test_def_matches_model(name):
    spec = DEFS[name]
    model = getattr(schemas, name)
    if "enum" in spec:
        assert issubclass(model, Enum)
        assert [m.value for m in model] == spec["enum"]
        return
    assert issubclass(model, BaseModel)
    generated = model.model_json_schema()
    assert set(generated["properties"]) == set(spec["properties"])
    assert set(generated.get("required", [])) == set(spec.get("required", []))


def test_constants():
    assert config.MAX_EXPLORE_LOOP == SPEC["constants"]["MAX_EXPLORE_LOOP"]
    assert config.MAX_REPLAN == SPEC["constants"]["MAX_REPLAN"]
    assert config.MAX_TOOL_CALLS == SPEC["constants"]["MAX_TOOL_CALLS"]
    assert config.MEMORY_WINDOW_N == SPEC["constants"]["MEMORY_WINDOW_N"]
