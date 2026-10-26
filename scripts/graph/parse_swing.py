"""swing 목업 소스(의사코드)의 @ 어노테이션을 읽어 공통 그래프 모델 형식의 노드/엣지를 만든다.

노드: Screen / TP / Batch / Module / DBIO / Table / Column
어노테이션 규약은 docs/mock_data.md 참고.
"""

import re
from collections import defaultdict
from pathlib import Path

from .model import ParseResult, new_edge

ANNOTATION = re.compile(r"^\s*(?://|--|#|/?\*+)\s*@(\w+)(?:\s+(.*?))?\s*$")
COLUMN_LINE = re.compile(r"^\s+([A-Z][A-Z0-9_]*)\s+(VARCHAR|NUMBER|CHAR|TIMESTAMP|CLOB|DATE)\b([^-]*)(?:--\s*(.*))?$")
PK_LINE = re.compile(r"PRIMARY KEY\s*\(([^)]*)\)")
CODE_ROW = re.compile(r"VALUES\s*\('(\w+)',\s*'([^']*)',\s*'([^']*)'")
CODE_GRP = re.compile(r"^--\s*\[(\w+)\]\s*(.*)$")
CODE_REF = re.compile(r"\(([A-Z][A-Z_]*_CD)\b")

LIST_PROPS = {"if": "interfaces", "image": "images", "rule": "rules"}
EDGE_ANN = {"calls": None, "reads": "READS", "writes": "WRITES", "references": "REFERENCES"}


class Parser:
    def __init__(self, root: Path, domain: str) -> None:
        self.root = root
        self.domain = domain
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.attach_code: dict[str, list[str]] = defaultdict(list)
        self.attach_props: dict[str, dict] = defaultdict(dict)
        self.errors: list[str] = []

    def parse_file(self, path: Path) -> None:
        rel = path.relative_to(self.root).as_posix()
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
                node = {"node_id": node_id, "label": label, "name": node_id.split(":", 1)[-1], "domain": self.domain,
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
                self.edges.append(new_edge(cur["node_id"], to_id, EDGE_ANN[key], f"{rel}:{line_no}",
                                           note=note, event=event or "", via=via.group(1) if via else ""))
            elif key == "desc" and event:
                evs = target["events"]
                evs[-1] = evs[-1] + f": {value}" if ": " not in evs[-1] else evs[-1] + f" {value}"
            elif key in LIST_PROPS:
                target.setdefault(LIST_PROPS[key], []).append(value)
            elif key == "name" and cur is not None and not cur.get("attach"):
                cur["name"] = value
            else:
                target[key] = f"{target[key]}\n{value}" if key in target else value

    def finalize(self) -> ParseResult:
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

        codes = self.load_codes()
        for node in list(self.nodes.values()):
            node["properties"]["code"] = "\n".join(node.pop("code")).strip("\n")
            if node["label"] == "Table":
                self.add_columns(node, codes)

        # @calls 의 엣지 타입은 노드 라벨로 결정: Screen 출발 또는 TP→TP 는 CALLS, 나머지는 INVOKES
        for e in self.edges:
            if e["type"] is None and e["from_id"] in self.nodes and e["to_id"] in self.nodes:
                src, dst = self.nodes[e["from_id"]]["label"], self.nodes[e["to_id"]]["label"]
                e["type"] = "CALLS" if src == "Screen" or (src, dst) == ("TP", "TP") else "INVOKES"
        return ParseResult(nodes=list(self.nodes.values()), edges=self.edges, errors=self.errors)

    def add_columns(self, table: dict, codes: dict) -> None:
        tname = table["node_id"].split(":", 1)[1]
        body = table["properties"]["code"]
        pks: set[str] = set()
        for m in PK_LINE.finditer(body):
            pks |= {c.strip() for c in m.group(1).split(",")}
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
            self.edges.append(new_edge(table["node_id"], nid, "HAS_COLUMN", table["source_file"]))

    def load_codes(self) -> dict:
        codes: dict[str, dict] = {}
        for path in sorted(self.root.rglob("code/*.sql")):
            for ln in path.read_text(encoding="utf-8").splitlines():
                m = CODE_GRP.match(ln)
                if m:
                    codes.setdefault(m.group(1), {"name": m.group(2).strip(), "values": []})
                    continue
                m = CODE_ROW.search(ln)
                if m and "TB_COMM_CD_DTL" in ln:
                    codes.setdefault(m.group(1), {"name": "", "values": []})["values"].append((m.group(2), m.group(3)))
        return codes


def parse(root: Path, domain: str = "swing") -> ParseResult:
    p = Parser(root, domain)
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            p.parse_file(path)
    return p.finalize()
