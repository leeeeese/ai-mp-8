"""영향도 그래프 공통 모델: 노드/엣지 종류, 허용 패턴, ID 규칙, 검증, 출력(csv·cypher), Neo4j 적재.

영역별 파서(parse_<domain>.py)는 아래 dict 형식으로 노드/엣지를 내보내고, 검증·출력은 여기서 한 번에 한다.
    node: {node_id, label, name, domain, source_file, source_line, properties: {..., code}}  (role 등은 enrich 가 추가)
    edge: {from_id, to_id, type, note, event, via, src}
source_file 은 해당 영역 소스 루트 기준 상대경로. ID 규칙은 docs/mock_data.md "노드 ID 규칙" 참고.
"""

import csv
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LABEL_PREFIX = {
    "Screen": "SCR",
    "Controller": "CTL",
    "Service": "SVC",
    "SQL": "SQL",
    "TP": "TP",
    "Module": "MOD",
    "Batch": "BAT",
    "DBIO": "DBIO",
    "Table": "TBL",
    "Column": "COL",
}
# 공통 역할 라벨: 영역마다 라벨이 달라도 Planner 의 목표 역할(target_role)로 같은 단계를 가리킨다
LABEL_ROLE = {
    "Screen": "Entry",
    "Batch": "Entry",
    "Controller": "EntryHandler",
    "TP": "EntryHandler",
    "Service": "Logic",
    "Module": "Logic",
    "SQL": "DataAccess",
    "DBIO": "DataAccess",
    "Table": "Table",
    "Column": "Column",
}
EDGE_TYPES = ("CALLS", "OPENS", "INVOKES", "EXECUTES", "READS", "WRITES", "REFERENCES", "HAS_COLUMN")

# 영향도 그래프 노드 구조도의 패턴 + 합의된 확장(★)
ALLOWED = {
    # 공통
    ("Screen", "OPENS", "Screen"),
    ("DBIO", "READS", "Table"),
    ("DBIO", "WRITES", "Table"),
    ("Table", "REFERENCES", "Table"),
    ("Table", "HAS_COLUMN", "Column"),
    # tos
    ("Screen", "CALLS", "Controller"),
    ("Controller", "INVOKES", "Service"),
    ("Controller", "EXECUTES", "SQL"),  # ★ Service 없이 매퍼를 직접 쓰는 Controller (예: 공통코드 조회)
    ("Service", "INVOKES", "Service"),
    ("Service", "EXECUTES", "SQL"),
    ("Batch", "EXECUTES", "SQL"),
    ("Batch", "INVOKES", "DBIO"),
    ("SQL", "READS", "Table"),
    ("SQL", "WRITES", "Table"),
    # swing
    ("Screen", "CALLS", "TP"),
    ("TP", "CALLS", "TP"),
    ("TP", "INVOKES", "Module"),
    ("TP", "INVOKES", "DBIO"),
    ("Module", "INVOKES", "Module"),
    ("Module", "INVOKES", "DBIO"),
    ("Batch", "INVOKES", "Module"),  # ★ 배치의 공통 모듈 호출
    ("Batch", "INVOKES", "TP"),  # ★ 배치의 서비스 재사용 (예: 구비서류 자동처리 → 결합 가입)
}


@dataclass
class ParseResult:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # --strict 에서 실패
    infos: list[str] = field(default_factory=list)  # 의도된 누락 등 참고 사항


def new_edge(from_id: str, to_id: str, type_: str | None, src: str, note: str = "", event: str = "", via: str = "") -> dict:
    return {"from_id": from_id, "to_id": to_id, "type": type_, "note": note, "event": event, "via": via, "src": src}


def finalize(results: list[ParseResult]) -> ParseResult:
    """영역별 결과를 합쳐 ID·끊긴 참조·패턴을 검증하고, READS/WRITES 의 columns 를 채운다."""
    out = ParseResult()
    nodes: dict[str, dict] = {}
    for r in results:
        out.errors += r.errors
        out.infos += r.infos
        for n in r.nodes:
            prefix = LABEL_PREFIX.get(n["label"])
            if prefix is None or not n["node_id"].startswith(prefix + ":") or re.search(r"\s", n["node_id"]):
                out.errors.append(f"ID 규칙 위반 {n['node_id']} (label {n['label']})")
            if n["node_id"] in nodes:
                prev = nodes[n["node_id"]]
                kind = "영역 간 ID 충돌" if prev["domain"] != n["domain"] else "중복 노드"
                out.errors.append(f"{kind} {n['node_id']}: {prev['domain']}/{prev['source_file']}:{prev['source_line']}, "
                                  f"{n['domain']}/{n['source_file']}:{n['source_line']}")
            nodes[n["node_id"]] = n

    table_cols: dict[str, list[str]] = {}
    for n in nodes.values():
        if n["label"] == "Column":
            table_cols.setdefault("TBL:" + n["properties"]["table"], []).append(n["name"])

    edges: list[dict] = []
    seen: dict[tuple, dict] = {}
    for r in results:
        for e in r.edges:
            if e["from_id"] not in nodes or e["to_id"] not in nodes:
                out.errors.append(f"끊긴 참조 {e['from_id']} -> {e['to_id']} ({e['src']})")
                continue
            src, dst = nodes[e["from_id"]], nodes[e["to_id"]]
            if e["type"] in ("READS", "WRITES") and dst["label"] == "Table":
                body = src["properties"].get("code", "")
                e["columns"] = ",".join(c for c in table_cols.get(dst["node_id"], []) if re.search(rf"\b{c}\b", body))
            key = (e["from_id"], e["to_id"], e["type"])
            if key in seen:
                prev = seen[key]
                if e["event"] and e["event"] not in prev["event"].split(","):
                    prev["event"] = ",".join(filter(None, [prev["event"], e["event"]]))
                continue
            seen[key] = e
            edges.append(e)

    for e in edges:
        pattern = (nodes[e["from_id"]]["label"], e["type"], nodes[e["to_id"]]["label"])
        if pattern not in ALLOWED:
            out.errors.append(f"규약 외 패턴 {pattern}: {e['from_id']} -> {e['to_id']} ({e['src']})")
        if e["type"] == "REFERENCES" and e["via"]:
            missing = [c for c in e["via"].split(",") if c not in table_cols.get(e["from_id"], [])]
            if missing:
                out.errors.append(f"{e['from_id']} REFERENCES via {','.join(missing)}: 컬럼 없음")

    out.nodes, out.edges = list(nodes.values()), edges
    return out


# 공통 여부: 이름이 Cmm/Comm/Common 으로 시작하거나 프로그램 설명이 기술 공통 요소(유틸·모듈·코드·기동기·클라이언트)인 노드.
# "상품 가입/해지 공통 서비스" 처럼 업무 처리를 묶은 서비스는 공통으로 보지 않는다.
COMMON_PREFIXES = ("Cmm", "Comm", "Common")
COMMON_DESC = re.compile(r"공통\s*(유틸|모듈|코드|기동기|클라이언트)")
SEARCH_PROPS = ("desc", "comment", "program_name", "program_desc", "code_grp_nm")


def enrich(graph: ParseResult, glossary: list[dict]) -> None:
    """역할(role), 공통 여부(is_common), 용어사전(terms), 검색 문자열(search_text)을 노드에 붙인다.

    glossary: [{term, synonyms: [..], node_id, domain}] (data/glossary/<domain>.csv)
    """
    by_id = {n["node_id"]: n for n in graph.nodes}
    for g in glossary:
        node = by_id.get(g["node_id"])
        if node is None:
            graph.errors.append(f"용어사전 {g['domain']}: 노드 없음 {g['node_id']} (용어 {g['term']})")
            continue
        terms = node["properties"].setdefault("terms", [])
        for t in [g["term"], *g["synonyms"]]:
            if t and t not in terms:
                terms.append(t)

    for n in graph.nodes:
        n["role"] = LABEL_ROLE[n["label"]]
        p = n["properties"]
        program = str(p.get("program", ""))
        p["is_common"] = n["label"] not in ("Table", "Column") and (
            program.startswith(COMMON_PREFIXES) or n["name"].startswith(COMMON_PREFIXES)
            or any(COMMON_DESC.search(str(p.get(k, ""))) for k in ("program_name", "program_desc", "desc")))
        key = n["node_id"].split(":", 1)[1]
        parts = [key, n["name"], *(str(p[k]) for k in SEARCH_PROPS if p.get(k)), *p.get("terms", [])]
        p["search_text"] = " ".join(dict.fromkeys(parts))


def node_props(n: dict) -> dict:
    """Neo4j 에 저장할 평탄화된 속성 (리스트는 Neo4j 리스트로 유지)."""
    return {"node_id": n["node_id"], "name": n["name"], "role": n["role"], "domain": n["domain"],
            "source_file": n["source_file"], "source_line": n["source_line"], **n["properties"]}


def edge_props(e: dict) -> dict:
    return {k: e[k] for k in ("note", "event", "via", "columns") if e.get(k)}


def write_csv(graph_dir: Path, nodes: list[dict], edges: list[dict]) -> None:
    with open(graph_dir / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "label", "role", "name", "domain", "source_file", "source_line", "properties"])
        for n in nodes:
            w.writerow([n["node_id"], n["label"], n["role"], n["name"], n["domain"], n["source_file"], n["source_line"],
                        json.dumps(n["properties"], ensure_ascii=False)])
    with open(graph_dir / "edges.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "to_id", "type", "properties"])
        for e in edges:
            w.writerow([e["from_id"], e["to_id"], e["type"], json.dumps(edge_props(e), ensure_ascii=False)])


def cypher_map(d: dict) -> str:
    return "{" + ", ".join(f"`{k}`: {json.dumps(v, ensure_ascii=False)}" for k, v in d.items()) + "}"


def write_cypher(graph_dir: Path, nodes: list[dict], edges: list[dict]) -> None:
    lines = [
        "// scripts/build_graph.py 가 생성한 파일. 직접 수정하지 마세요.",
        "MATCH (n:Node) DETACH DELETE n;",
        "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE;",
        "DROP INDEX node_search IF EXISTS;",
        FULLTEXT_INDEX + ";",
        "",
    ]
    for n in nodes:
        lines.append(f"CREATE (:Node:{n['label']}:{n['role']} {cypher_map(node_props(n))});")
    lines.append("")
    for e in edges:
        props = edge_props(e)
        lines.append(f"MATCH (a:Node {{node_id: {json.dumps(e['from_id'])}}}), (b:Node {{node_id: {json.dumps(e['to_id'])}}}) "
                     f"CREATE (a)-[:{e['type']}{' ' + cypher_map(props) if props else ''}]->(b);")
    (graph_dir / "seed.cypher").write_text("\n".join(lines) + "\n", encoding="utf-8")


# 노드명·설명·컬럼 코멘트·용어사전을 묶은 search_text 검색용. cjk 분석기는 한글을 2글자 단위로 나눠 부분 일치("광고" → "광고여부")를 지원한다
FULLTEXT_INDEX = ("CREATE FULLTEXT INDEX node_search IF NOT EXISTS FOR (n:Node) ON EACH [n.search_text] "
                  "OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}")


def load_neo4j(nodes: list[dict], edges: list[dict]) -> None:
    """기존 :Node 그래프를 지우고 전체를 다시 적재한다 (.env 의 NEO4J_* 사용)."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    with driver, driver.session() as s:
        s.run("MATCH (n:Node) DETACH DELETE n")
        s.run("CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE")
        s.run("DROP INDEX node_search IF EXISTS")
        s.run(FULLTEXT_INDEX)
        for label in sorted({n["label"] for n in nodes}):
            rows = [node_props(n) for n in nodes if n["label"] == label]
            s.run(f"UNWIND $rows AS r CREATE (n:Node:{label}:{LABEL_ROLE[label]}) SET n = r", rows=rows)
        for rel in sorted({e["type"] for e in edges}):
            rows = [{"from_id": e["from_id"], "to_id": e["to_id"], "props": edge_props(e)} for e in edges if e["type"] == rel]
            s.run(f"UNWIND $rows AS r MATCH (a:Node {{node_id: r.from_id}}), (b:Node {{node_id: r.to_id}}) "
                  f"CREATE (a)-[x:{rel}]->(b) SET x = r.props", rows=rows)
