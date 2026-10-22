"""data/ground_truth/*.json 의 노드 ID 가 data/graph/nodes.csv 에 존재하는지 검사한다.

usage: python scripts/build_graph_seed.py && python scripts/check_ground_truth.py
"""

import csv
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GT_DIR = ROOT / "data" / "ground_truth"
NODES_CSV = ROOT / "data" / "graph" / "nodes.csv"
EDGES_CSV = ROOT / "data" / "graph" / "edges.csv"
EXPLORER_MAX_HOPS = 2


def hop_distances(starts: list[str], adj: dict[str, set[str]]) -> dict[str, int]:
    """시작 노드들로부터 방향 무시 최단 hop 수."""
    dist = {s: 0 for s in starts}
    q = deque(starts)
    while q:
        cur = q.popleft()
        for nxt in adj[cur]:
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


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

    adj: dict[str, set[str]] = defaultdict(set)
    with open(EDGES_CSV, encoding="utf-8") as f:
        for e in csv.DictReader(f):
            adj[e["from_id"]].add(e["to_id"])
            adj[e["to_id"]].add(e["from_id"])

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
        dist = hop_distances(gt["expected"]["start_nodes"], adj)
        for it in gt["expected"]["impacted"]:
            d = dist.get(it["node_id"])
            if it["must_find"] and (d is None or d > EXPLORER_MAX_HOPS):
                print(f"  [INFO] must_find {it['node_id']} : 시작 노드에서 {d if d is not None else '도달불가'} hop")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
