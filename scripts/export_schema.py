"""Pydantic 모델로 JSON Schema 를 다시 생성해 schemas/schemas.json 과 비교할 수 있게 출력한다.

usage:
    python scripts/export_schema.py > /tmp/generated.json   # 비교용 출력
    python scripts/export_schema.py --write                 # schemas/schemas.json 의 $defs 를 모델 기준으로 갱신 (constants 유지)
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
# 기존 정의 순서를 유지하고, 새로 생긴 공개 모델은 뒤에 붙인다 (삭제된 모델은 빠짐)
names = [n for n in spec["$defs"] if n in schemas.__all__] + [n for n in schemas.__all__ if n not in spec["$defs"]]
for name in names:
    model = getattr(schemas, name)
    if isinstance(model, type) and issubclass(model, BaseModel):
        s = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(s.pop("$defs", {}))
        defs[name] = s
    elif isinstance(model, type) and issubclass(model, Enum):
        defs[name] = {"enum": [m.value for m in model], "title": name, "type": "string"}

out = {"$schema": spec["$schema"], "title": spec["title"], "constants": spec["constants"], "$defs": defs}
text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
if "--write" in sys.argv:
    config.SCHEMA_PATH.write_text(text, encoding="utf-8")
else:
    print(text, end="")
