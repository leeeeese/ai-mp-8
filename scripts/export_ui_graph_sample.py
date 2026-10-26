"""UI mock 용 그래프 샘플을 만든다: data/graph/*.csv → frontend/src/mocks/data/graph-sample.json

영역별(dbm = tos, prod = swing) 노드·엣지와 품질 점검 값. 소스 원문(code)은 넣지 않는다.

usage: python scripts/build_graph.py && python scripts/export_ui_graph_sample.py
"""

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "data" / "graph"
OUT = ROOT / "frontend" / "src" / "mocks" / "data" / "graph-sample.json"
SM = {"tos": "dbm", "swing": "prod"}
KEEP_PROPS = ("desc", "comment", "program", "url", "stmt_type", "is_common", "table", "interfaces")
KEEP_EDGE = ("columns", "via", "event", "note")


def main() -> None:
    nodes = list(csv.DictReader(open(GRAPH_DIR / "nodes.csv", encoding="utf-8")))
    edges = list(csv.DictReader(open(GRAPH_DIR / "edges.csv", encoding="utf-8")))
    out = {}
    for domain, sm in SM.items():
        ns = [n for n in nodes if n["domain"] == domain]
        ids = {n["node_id"] for n in ns}
        nn = []
        for n in ns:
            p = json.loads(n["properties"])
            nn.append({"id": n["node_id"], "label": n["label"], "role": n["role"], "name": n["name"],
                       "sourceFile": n["source_file"], "sourceLine": int(n["source_line"]),
                       **{k: p[k] for k in KEEP_PROPS if p.get(k) not in (None, "", [])}})
        ee = [{"from": e["from_id"], "to": e["to_id"], "type": e["type"],
               **{k: v for k, v in json.loads(e["properties"]).items() if k in KEEP_EDGE}}
              for e in edges if e["from_id"] in ids]
        degree = Counter([e["from"] for e in ee] + [e["to"] for e in ee])
        out[sm] = {
            "domain": domain,
            "nodes": nn,
            "edges": ee,
            "labels": dict(Counter(n["label"] for n in nn)),
            "checks": {
                "isolated": [n["id"] for n in nn if degree[n["id"]] == 0],
                "rwNoColumns": [f'{e["from"]} → {e["to"]}' for e in ee if e["type"] in ("READS", "WRITES") and not e.get("columns")],
                "noSourceFile": [n["id"] for n in nn if not n["sourceFile"]],
            },
        }
        print(f"{domain} ({sm}): nodes {len(nn)}, edges {len(ee)}")
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
