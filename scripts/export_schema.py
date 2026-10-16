"""Pydantic 모델로 JSON Schema 를 다시 생성해 schemas/schemas.json 과 비교할 수 있게 출력한다.

usage: python scripts/export_schema.py > /tmp/generated.json
"""

import json
import sys
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from impact_agent import config, schemas  # noqa: E402

spec = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
defs = {}
for name in spec["$defs"]:
    model = getattr(schemas, name)
    if isinstance(model, type) and issubclass(model, BaseModel):
        s = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(s.pop("$defs", {}))
        defs[name] = s
    elif isinstance(model, type) and issubclass(model, Enum):
        defs[name] = {"enum": [m.value for m in model], "title": name, "type": "string"}

out = {"$schema": spec["$schema"], "title": spec["title"], "constants": spec["constants"], "$defs": defs}
print(json.dumps(out, ensure_ascii=False, indent=2))
