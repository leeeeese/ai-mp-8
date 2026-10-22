"""data/ground_truth/*.json 의 노드 ID 가 data/graph/nodes.csv 에 존재하는지 검사한다.

usage: python scripts/build_graph_seed.py && python scripts/check_ground_truth.py
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GT_DIR = ROOT / "data" / "ground_truth"
NODES_CSV = ROOT / "data" / "graph" / "nodes.csv"


def referenced_ids(gt: dict) -> list[tuple[str, str]]:
    exp = gt["expected"]
    refs = [("start_nodes", n) for n in exp.get("start_nodes", [])]
    refs += [("impacted", i["node_id"]) for i in exp.get("impacted", [])]
    refs += [("reference_only", n) for n in exp.get("reference_only", [])]
    refs += [("distractors", d["node_id"]) for d in gt.get("distractors", [])]
    return refs


def main() -> int:
    with open(NODES_CSV, encoding="utf-8") as f:
        node_ids = {row["node_id"] for row in csv.DictReader(f)}

    errors = 0
    for path in sorted(GT_DIR.glob("*.json")):
        gt = json.loads(path.read_text(encoding="utf-8"))
        missing = [(field, nid) for field, nid in referenced_ids(gt) if nid not in node_ids]
        overlap = {i["node_id"] for i in gt["expected"].get("impacted", [])} & {d["node_id"] for d in gt.get("distractors", [])}
        for field, nid in missing:
            print(f"[ERROR] {path.name}: {field} 의 {nid} 가 그래프에 없음")
        for nid in overlap:
            print(f"[ERROR] {path.name}: {nid} 가 impacted 와 distractors 에 동시에 있음")
        errors += len(missing) + len(overlap)
        if not missing and not overlap:
            print(f"[OK] {path.name}: {len(referenced_ids(gt))} refs")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
