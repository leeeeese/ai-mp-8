"""eval/ground_truth/*.json 이 그래프(data/graph)와 맞는지 검사한다.

- 정답 경로(expected.paths)·오답 경로(wrong_paths)의 노드가 존재하고, 이웃한 두 노드가 from → to 엣지로 이어지는지
- impacted / reference_only / start_nodes / distractors 의 노드 존재, enum 값
- distractors 가 impacted 에 섞이지 않았는지 (정답 경로 위 노드는 [INFO]: 경로는 지나지만 노드 자체는 변경 대상 아님)
- [INFO] 시작 노드에서 2hop(Explorer 기본 탐색 범위) 밖에 있는 must_find 노드

usage: python scripts/build_graph.py && python scripts/check_ground_truth.py [SC-001 ...]
"""

import csv
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "data" / "graph"
GT_DIR = ROOT / "eval" / "ground_truth"

AREAS = {"screen", "db", "interface", "batch"}
IMPACT_TYPES = {"add", "modify", "reference_only", "none"}
FEASIBILITY = {"feasible", "hard", "review"}
OWNERS = {"business", "developer"}
PATH_STATUS = {"confirmed", "pending"}


def load_graph() -> tuple[set[str], set[tuple[str, str]], dict[str, list[str]]]:
    nodes = {r["node_id"] for r in csv.DictReader(open(GRAPH_DIR / "nodes.csv", encoding="utf-8"))}
    edges, adj = set(), {}
    for e in csv.DictReader(open(GRAPH_DIR / "edges.csv", encoding="utf-8")):
        edges.add((e["from_id"], e["to_id"]))
        adj.setdefault(e["from_id"], []).append(e["to_id"])
    return nodes, edges, adj


def hops_from(starts: list[str], adj: dict[str, list[str]]) -> dict[str, int]:
    dist = {s: 0 for s in starts}
    q = deque(starts)
    while q:
        n = q.popleft()
        for m in adj.get(n, []):
            if m not in dist:
                dist[m] = dist[n] + 1
                q.append(m)
    return dist


def check(path: Path, nodes: set[str], edges: set[tuple[str, str]], adj: dict) -> tuple[list[str], list[str]]:
    gt = json.loads(path.read_text(encoding="utf-8"))
    exp = gt["expected"]
    errs, infos = [], []

    def need(nid: str, where: str) -> None:
        if nid not in nodes:
            errs.append(f"{where}: 노드 없음 {nid}")

    if exp["feasibility"] not in FEASIBILITY:
        errs.append(f"feasibility 값 {exp['feasibility']}")
    for area, v in exp["areas"].items():
        if area not in AREAS or v not in IMPACT_TYPES:
            errs.append(f"areas {area}={v}")
    for nid in exp["start_nodes"]:
        need(nid, "start_nodes")
    for it in exp["impacted"]:
        need(it["node_id"], "impacted")
        if it["area"] not in AREAS or it["impact_type"] not in IMPACT_TYPES:
            errs.append(f"impacted {it['node_id']}: area/impact_type 값")
    for nid in exp.get("reference_only", []):
        need(nid, "reference_only")
    for o in exp.get("open_items", []):
        if o["owner"] not in OWNERS:
            errs.append(f"open_items owner {o['owner']}")

    path_nodes: set[str] = set()
    for kind, plist in (("paths", exp["paths"]), ("wrong_paths", gt.get("wrong_paths", []))):
        for p in plist:
            pid = p["path_id"]
            if kind == "paths" and p.get("status", "confirmed") not in PATH_STATUS:
                errs.append(f"{pid}: status {p['status']}")
            for nid in p["nodes"]:
                need(nid, f"{kind} {pid}")
            for a, b in zip(p["nodes"], p["nodes"][1:]):
                if (a, b) not in edges:
                    errs.append(f"{kind} {pid}: 엣지 없음 {a} -> {b}")
            if kind == "paths":
                path_nodes |= set(p["nodes"])

    impacted = {it["node_id"] for it in exp["impacted"]}
    for d in gt.get("distractors", []):
        need(d["node_id"], "distractors")
        if d["node_id"] in impacted:
            errs.append(f"distractors {d['node_id']}: impacted 에도 있음")
        elif d["node_id"] in path_nodes:
            infos.append(f"distractors {d['node_id']}: 정답 경로 위 노드 (노드 자체는 변경 대상 아님)")

    dist = hops_from(exp["start_nodes"], adj)
    for it in exp["impacted"]:
        if it.get("must_find") and dist.get(it["node_id"], 99) > 2:
            hop = dist.get(it["node_id"])
            infos.append(f"must_find {it['node_id']}: 시작 노드에서 {hop if hop is not None else '도달 불가'}hop")
    return errs, infos


def main() -> int:
    nodes, edges, adj = load_graph()
    targets = [GT_DIR / f"{a}.json" for a in sys.argv[1:]] or sorted(GT_DIR.glob("*.json"))
    failed = 0
    for path in targets:
        errs, infos = check(path, nodes, edges, adj)
        print(f"{path.stem}: {'OK' if not errs else f'오류 {len(errs)}'}")
        for e in errs:
            print(f"  [ERR] {e}")
        for i in infos:
            print(f"  [INFO] {i}")
        failed += bool(errs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
