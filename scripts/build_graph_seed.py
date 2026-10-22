"""목업 소스(data/sources)의 @ 어노테이션을 읽어 Neo4j 그래프 시드를 생성한다.

어노테이션 규약은 data/README.md 참고.

usage:
    python scripts/build_graph_seed.py            # data/graph/{nodes.csv, edges.csv, seed.cypher} 생성
    python scripts/build_graph_seed.py --load     # 생성 후 .env 의 Neo4j 에 적재 (기존 그래프 삭제)
    python scripts/build_graph_seed.py --strict   # 끊긴 참조가 있으면 실패(exit 1)
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES_DIR = ROOT / "data" / "sources"
GRAPH_DIR = ROOT / "data" / "graph"

ANNOTATION = re.compile(r"^\s*(?://|--|#|/?\*+)\s*@(\w+)\s+(.+?)\s*$")
EDGE_TYPES = {"calls": "CALLS", "references": "REFERENCES", "updates": "UPDATES", "flows_to": "FLOWS_TO"}
INHERITED_PROPS = ("sm_part",)


def parse_file(path: Path, domain: str) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    parent: dict | None = None  # 직전 @node
    current: dict | None = None  # 엣지/속성이 붙을 노드 (@member 가 있으면 그 노드)
    rel_path = path.relative_to(ROOT).as_posix()

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = ANNOTATION.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)

        if key in ("node", "member"):
            label, node_id = value.split(maxsplit=1)
            node = {
                "node_id": node_id.strip(),
                "label": label,
                "name": node_id.split(":", 1)[-1].strip(),
                "domain": domain,
                "source_file": rel_path,
                "source_line": line_no,
                "properties": {},
            }
            if key == "member":
                if parent is None:
                    raise ValueError(f"{rel_path}:{line_no} @member 앞에 @node 가 없습니다.")
                for p in INHERITED_PROPS:
                    if p in parent["properties"]:
                        node["properties"][p] = parent["properties"][p]
                edges.append({"from_id": parent["node_id"], "to_id": node["node_id"], "type": "CONTAINS", "note": ""})
            else:
                parent = node
            current = node
            nodes.append(node)
            continue

        if current is None:
            raise ValueError(f"{rel_path}:{line_no} @{key} 앞에 @node 가 없습니다.")

        if key in EDGE_TYPES:
            target, _, note = value.partition(" ")
            edges.append({"from_id": current["node_id"], "to_id": target, "type": EDGE_TYPES[key], "note": note.strip()})
        elif key == "name":
            current["name"] = value
        else:
            props = current["properties"]
            props[key] = f"{props[key]}\n{value}" if key in props else value

    return nodes, edges


def collect() -> tuple[list[dict], list[dict], list[str]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    errors: list[str] = []

    for domain_dir in sorted(p for p in SOURCES_DIR.iterdir() if p.is_dir()):
        for path in sorted(domain_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            file_nodes, file_edges = parse_file(path, domain_dir.name)
            for n in file_nodes:
                if n["node_id"] in nodes:
                    prev = nodes[n["node_id"]]
                    errors.append(f"중복 노드 {n['node_id']}: {prev['source_file']}:{prev['source_line']}, "
                                  f"{n['source_file']}:{n['source_line']}")
                    continue
                nodes[n["node_id"]] = n
            edges.extend(file_edges)

    seen = set()
    valid_edges = []
    for e in edges:
        if e["to_id"] not in nodes:
            src = nodes.get(e["from_id"], {})
            errors.append(f"끊긴 참조 {e['from_id']} -[{e['type']}]-> {e['to_id']} ({src.get('source_file', '?')})")
            continue
        key = (e["from_id"], e["to_id"], e["type"])
        if key not in seen:
            seen.add(key)
            valid_edges.append(e)

    return list(nodes.values()), valid_edges, errors


def write_csv(nodes: list[dict], edges: list[dict]) -> None:
    with open(GRAPH_DIR / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "label", "name", "domain", "source_file", "source_line", "properties"])
        for n in nodes:
            w.writerow([n["node_id"], n["label"], n["name"], n["domain"], n["source_file"], n["source_line"],
                        json.dumps(n["properties"], ensure_ascii=False)])
    with open(GRAPH_DIR / "edges.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "to_id", "type", "note"])
        for e in edges:
            w.writerow([e["from_id"], e["to_id"], e["type"], e["note"]])


def node_props(n: dict) -> dict:
    """Neo4j 에 저장할 평탄화된 속성."""
    return {"node_id": n["node_id"], "name": n["name"], "domain": n["domain"], "source_file": n["source_file"],
            "source_line": n["source_line"], **n["properties"]}


def cypher_map(d: dict) -> str:
    return "{" + ", ".join(f"`{k}`: {json.dumps(v, ensure_ascii=False)}" for k, v in d.items()) + "}"


def write_cypher(nodes: list[dict], edges: list[dict]) -> None:
    lines = [
        "// scripts/build_graph_seed.py 가 생성한 파일. 직접 수정하지 마세요.",
        "MATCH (n:Node) DETACH DELETE n;",
        "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE;",
        "",
    ]
    for n in nodes:
        lines.append(f"CREATE (:Node:{n['label']} {cypher_map(node_props(n))});")
    lines.append("")
    for e in edges:
        note = f" {{note: {json.dumps(e['note'], ensure_ascii=False)}}}" if e["note"] else ""
        lines.append(f"MATCH (a:Node {{node_id: {json.dumps(e['from_id'])}}}), (b:Node {{node_id: {json.dumps(e['to_id'])}}}) "
                     f"CREATE (a)-[:{e['type']}{note}]->(b);")
    (GRAPH_DIR / "seed.cypher").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_neo4j(nodes: list[dict], edges: list[dict]) -> None:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(ROOT / ".env")
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    with driver, driver.session() as s:
        s.run("MATCH (n:Node) DETACH DELETE n")
        s.run("CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE")
        for label in sorted({n["label"] for n in nodes}):
            rows = [node_props(n) for n in nodes if n["label"] == label]
            s.run(f"UNWIND $rows AS r CREATE (n:Node:{label}) SET n = r", rows=rows)
        for rel in sorted({e["type"] for e in edges}):
            rows = [e for e in edges if e["type"] == rel]
            s.run(f"UNWIND $rows AS r MATCH (a:Node {{node_id: r.from_id}}), (b:Node {{node_id: r.to_id}}) "
                  f"CREATE (a)-[x:{rel}]->(b) SET x.note = r.note", rows=rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true", help="생성 후 Neo4j 에 적재")
    ap.add_argument("--strict", action="store_true", help="오류(끊긴 참조/중복 노드)가 있으면 exit 1")
    args = ap.parse_args()

    nodes, edges, errors = collect()
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(nodes, edges)
    write_cypher(nodes, edges)

    print(f"nodes {len(nodes)}: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(n['label'] for n in nodes).items())))
    print(f"edges {len(edges)}: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(e['type'] for e in edges).items())))
    for err in errors:
        print(f"[WARN] {err}", file=sys.stderr)

    if args.load:
        load_neo4j(nodes, edges)
        print("Neo4j 적재 완료")
    return 1 if args.strict and errors else 0


if __name__ == "__main__":
    sys.exit(main())
