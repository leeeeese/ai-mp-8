"""목업 소스(data/sources)의 @ 어노테이션을 읽어 Neo4j 영향도 그래프 시드를 생성한다.

노드: Screen / TP / Batch / Module / DBIO / Table / Column
엣지: CALLS / INVOKES / READS / WRITES / REFERENCES / HAS_COLUMN
어노테이션 규약은 data/README.md 참고.

usage:
    python scripts/build_graph_seed.py            # data/graph/{nodes.csv, edges.csv, seed.cypher} 생성
    python scripts/build_graph_seed.py --load     # 생성 후 .env 의 Neo4j 에 적재 (기존 그래프 삭제)
    python scripts/build_graph_seed.py --strict   # 끊긴 참조 / 규약 외 패턴이 있으면 실패(exit 1)
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES_DIR = ROOT / "data" / "sources"
GRAPH_DIR = ROOT / "data" / "graph"

ANNOTATION = re.compile(r"^\s*(?://|--|#|/?\*+)\s*@(\w+)(?:\s+(.*?))?\s*$")
COLUMN_LINE = re.compile(r"^\s+([A-Z][A-Z0-9_]*)\s+(VARCHAR|NUMBER|CHAR|TIMESTAMP|CLOB|DATE)\b([^-]*)(?:--\s*(.*))?$")
PK_LINE = re.compile(r"PRIMARY KEY\s*\(([^)]*)\)")
CODE_ROW = re.compile(r"VALUES\s*\('(\w+)',\s*'([^']*)',\s*'([^']*)'")
CODE_GRP = re.compile(r"^--\s*\[(\w+)\]\s*(.*)$")
CODE_REF = re.compile(r"\(([A-Z][A-Z_]*_CD)\b")

LIST_PROPS = {"if": "interfaces", "image": "images", "rule": "rules"}
EDGE_ANN = {"calls": None, "reads": "READS", "writes": "WRITES", "references": "REFERENCES"}

# 사진(영향도 그래프 노드 구조)의 swing 패턴 + 합의된 확장(★)
ALLOWED = {
    ("Screen", "CALLS", "TP"),
    ("TP", "CALLS", "TP"),
    ("TP", "INVOKES", "Module"),
    ("TP", "INVOKES", "DBIO"),
    ("Module", "INVOKES", "Module"),
    ("Module", "INVOKES", "DBIO"),
    ("Batch", "INVOKES", "DBIO"),
    ("Batch", "INVOKES", "Module"),  # ★ 배치의 공통 모듈 호출
    ("Batch", "INVOKES", "TP"),  # ★ 배치의 서비스 재사용 (예: 구비서류 자동처리 → 결합 가입)
    ("DBIO", "READS", "Table"),
    ("DBIO", "WRITES", "Table"),
    ("Table", "REFERENCES", "Table"),
    ("Table", "HAS_COLUMN", "Column"),
}


class Parser:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.attach_code: dict[str, list[str]] = defaultdict(list)
        self.attach_props: dict[str, dict] = defaultdict(dict)
        self.errors: list[str] = []

    def parse_file(self, path: Path, domain: str) -> None:
        rel = path.relative_to(ROOT).as_posix()
        group: dict | None = None  # @group 공통 속성 (노드 아님)
        cur: dict | None = None  # 엣지/속성이 붙을 노드
        event: str | None = None
        code_lines: list[str] | None = None

        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            m = ANNOTATION.match(line)
            if not m:
                if code_lines is not None:
                    code_lines.append(line)
                continue
            key, value = m.group(1), (m.group(2) or "")

            if key == "group":
                group = {"id": value, "props": {}}
                cur, event, code_lines = None, None, None
                continue

            if key in ("node", "member", "attach"):
                event = None
                if key == "attach":
                    cur = {"node_id": value, "attach": True, "properties": self.attach_props[value]}
                    code_lines = self.attach_code[value]
                    if group:
                        cur["properties"].setdefault("program", group["id"].split(":", 1)[-1])
                    continue
                label, node_id = value.split(maxsplit=1)
                node = {"node_id": node_id, "label": label, "name": node_id.split(":", 1)[-1], "domain": domain,
                        "source_file": rel, "source_line": line_no, "properties": {}, "code": []}
                if key == "member" and group:
                    node["properties"]["program"] = group["id"].split(":", 1)[-1]
                    for k, v in group["props"].items():
                        node["properties"]["program_" + k if k in ("name", "desc") else k] = v
                elif key == "node":
                    group = None
                if node_id in self.nodes:
                    prev = self.nodes[node_id]
                    self.errors.append(f"중복 노드 {node_id}: {prev['source_file']}:{prev['source_line']}, {rel}:{line_no}")
                self.nodes[node_id] = node
                cur, code_lines = node, node["code"]
                continue

            target = cur["properties"] if cur else (group["props"] if group else None)
            if target is None:
                self.errors.append(f"{rel}:{line_no} @{key} 앞에 @node/@member/@group 이 없습니다.")
                continue

            if key == "event":
                name, _, desc = value.partition(" ")
                event = name
                target.setdefault("events", []).append(name + (f": {desc}" if desc else ""))
            elif key in EDGE_ANN:
                if cur is None:
                    self.errors.append(f"{rel}:{line_no} @{key} 는 노드에만 붙일 수 있습니다.")
                    continue
                to_id, _, note = value.partition(" ")
                note = note.strip()
                via = re.match(r"via\s+(\w+)", note)
                self.edges.append({"from_id": cur["node_id"], "to_id": to_id, "type": EDGE_ANN[key],
                                   "note": note, "event": event or "", "via": via.group(1) if via else "",
                                   "src": f"{rel}:{line_no}"})
            elif key == "desc" and event:
                evs = target["events"]
                evs[-1] = evs[-1] + f": {value}" if ": " not in evs[-1] else evs[-1] + f" {value}"
            elif key in LIST_PROPS:
                target.setdefault(LIST_PROPS[key], []).append(value)
            elif key == "name" and cur is not None and not cur.get("attach"):
                cur["name"] = value
            else:
                target[key] = f"{target[key]}\n{value}" if key in target else value

    def finalize(self) -> tuple[list[dict], list[dict]]:
        # @attach 로 흡수한 본문/속성 병합
        for nid, props in self.attach_props.items():
            if nid not in self.nodes:
                self.errors.append(f"@attach 대상 노드 없음: {nid}")
                continue
            node = self.nodes[nid]
            for k, v in props.items():
                if isinstance(v, list):
                    node["properties"].setdefault(k, []).extend(v)
                elif k in node["properties"]:
                    node["properties"][k] = f"{node['properties'][k]}\n{v}"
                else:
                    node["properties"][k] = v
            node["code"].extend(["", f"// ---- {props.get('program', '')} ----"] + self.attach_code[nid])

        codes = load_codes()
        for node in list(self.nodes.values()):
            node["properties"]["code"] = "\n".join(node.pop("code")).strip("\n")
            if node["label"] == "Table":
                self.add_columns(node, codes)

        edges = []
        seen = set()
        for e in self.edges:
            if e["to_id"] not in self.nodes or e["from_id"] not in self.nodes:
                self.errors.append(f"끊긴 참조 {e['from_id']} -> {e['to_id']} ({e['src']})")
                continue
            src, dst = self.nodes[e["from_id"]], self.nodes[e["to_id"]]
            if e["type"] is None:
                e["type"] = "CALLS" if src["label"] == "Screen" or (src["label"], dst["label"]) == ("TP", "TP") else "INVOKES"
            if e["type"] in ("READS", "WRITES") and dst["label"] == "Table":
                cols = dst["properties"]["column_names"]
                body = src["properties"].get("code", "")
                e["columns"] = ",".join(c for c in cols if re.search(rf"\b{c}\b", body))
            key = (e["from_id"], e["to_id"], e["type"])
            if key in seen:
                prev = next(x for x in edges if (x["from_id"], x["to_id"], x["type"]) == key)
                if e["event"] and e["event"] not in prev["event"].split(","):
                    prev["event"] = ",".join(filter(None, [prev["event"], e["event"]]))
                continue
            seen.add(key)
            edges.append(e)

        for e in edges:
            pattern = (self.nodes[e["from_id"]]["label"], e["type"], self.nodes[e["to_id"]]["label"])
            if pattern not in ALLOWED:
                self.errors.append(f"규약 외 패턴 {pattern}: {e['from_id']} -> {e['to_id']} ({e.get('src', '')})")
        for n in self.nodes.values():
            n["properties"].pop("column_names", None)
        return list(self.nodes.values()), edges

    def add_columns(self, table: dict, codes: dict) -> None:
        tname = table["node_id"].split(":", 1)[1]
        body = table["properties"]["code"]
        pks: set[str] = set()
        for m in PK_LINE.finditer(body):
            pks |= {c.strip() for c in m.group(1).split(",")}
        names = []
        for ln in body.splitlines():
            m = COLUMN_LINE.match(ln)
            if not m:
                continue
            col, dtype, rest, comment = m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip()
            size = re.match(r"\s*(\([^)]*\))?", rest).group(1) or ""
            props = {"data_type": dtype + size, "nullable": "NOT NULL" not in rest, "pk": col in pks, "comment": comment}
            grp = CODE_REF.search(comment)
            grp_cd = grp.group(1) if grp else (col if col in codes else None)
            if grp_cd and grp_cd in codes:
                props["code_grp"] = grp_cd
                props["code_grp_nm"] = codes[grp_cd]["name"]
                props["code_values"] = ", ".join(f"{c}={n}" for c, n in codes[grp_cd]["values"])
            nid = f"COL:{tname}.{col}"
            self.nodes[nid] = {"node_id": nid, "label": "Column", "name": col, "domain": table["domain"],
                               "source_file": table["source_file"], "source_line": table["source_line"],
                               "properties": {"table": tname, **props}}
            self.edges.append({"from_id": table["node_id"], "to_id": nid, "type": "HAS_COLUMN", "note": "",
                               "event": "", "via": "", "src": table["source_file"]})
            names.append(col)
        table["properties"]["column_names"] = names
        for e in self.edges:
            if e["from_id"] == table["node_id"] and e["type"] == "REFERENCES" and e["via"] and e["via"] not in names:
                self.errors.append(f"{table['node_id']} REFERENCES via {e['via']}: 컬럼 없음")


def load_codes() -> dict:
    codes: dict[str, dict] = {}
    for path in sorted(SOURCES_DIR.rglob("code/*.sql")):
        for ln in path.read_text(encoding="utf-8").splitlines():
            m = CODE_GRP.match(ln)
            if m:
                codes.setdefault(m.group(1), {"name": m.group(2).strip(), "values": []})
                continue
            m = CODE_ROW.search(ln)
            if m and "TB_COMM_CD_DTL" in ln:
                codes.setdefault(m.group(1), {"name": "", "values": []})["values"].append((m.group(2), m.group(3)))
    return codes


def collect() -> tuple[list[dict], list[dict], list[str]]:
    p = Parser()
    for domain_dir in sorted(d for d in SOURCES_DIR.iterdir() if d.is_dir()):
        for path in sorted(domain_dir.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                p.parse_file(path, domain_dir.name)
    nodes, edges = p.finalize()
    return nodes, edges, p.errors


def node_props(n: dict) -> dict:
    """Neo4j 에 저장할 평탄화된 속성 (리스트는 Neo4j 리스트로 유지)."""
    return {"node_id": n["node_id"], "name": n["name"], "domain": n["domain"], "source_file": n["source_file"],
            "source_line": n["source_line"], **n["properties"]}


def edge_props(e: dict) -> dict:
    return {k: e[k] for k in ("note", "event", "via", "columns") if e.get(k)}


def write_csv(nodes: list[dict], edges: list[dict]) -> None:
    with open(GRAPH_DIR / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "label", "name", "domain", "source_file", "source_line", "properties"])
        for n in nodes:
            w.writerow([n["node_id"], n["label"], n["name"], n["domain"], n["source_file"], n["source_line"],
                        json.dumps(n["properties"], ensure_ascii=False)])
    with open(GRAPH_DIR / "edges.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "to_id", "type", "properties"])
        for e in edges:
            w.writerow([e["from_id"], e["to_id"], e["type"], json.dumps(edge_props(e), ensure_ascii=False)])


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
        props = edge_props(e)
        lines.append(f"MATCH (a:Node {{node_id: {json.dumps(e['from_id'])}}}), (b:Node {{node_id: {json.dumps(e['to_id'])}}}) "
                     f"CREATE (a)-[:{e['type']}{' ' + cypher_map(props) if props else ''}]->(b);")
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
            rows = [{"from_id": e["from_id"], "to_id": e["to_id"], "props": edge_props(e)} for e in edges if e["type"] == rel]
            s.run(f"UNWIND $rows AS r MATCH (a:Node {{node_id: r.from_id}}), (b:Node {{node_id: r.to_id}}) "
                  f"CREATE (a)-[x:{rel}]->(b) SET x = r.props", rows=rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true", help="생성 후 Neo4j 에 적재")
    ap.add_argument("--strict", action="store_true", help="오류가 있으면 exit 1")
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
