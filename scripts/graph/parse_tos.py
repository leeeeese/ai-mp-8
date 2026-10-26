"""tos 소스를 실제 코드 관례대로 읽어 공통 그래프 모델 형식의 노드/엣지를 만든다 (그래프용 주석 없음).

    Screen     online/webapp/WEB-INF/jsp/**/<화면ID>.jsp (+ <script src> 로 연결한 js)
    Controller online/java/**/web/*Controller.java 의 @Get/@Post/@RequestMapping 메서드 (화면을 반환하는 메서드 제외)
    Service    online/java/**/service/*Service.java 의 메서드
    SQL        online/resources/mapper/**/*.xml 의 select/insert/update/delete,
               batch/proc/*.pc 의 EXEC SQL, batch/job/*.xml 의 type="SQL" 단계
    Batch      batch/job/*.xml (PROC 단계의 batch/proc/<program>.pc 를 흡수)
    DBIO       batch/dbio/*.dbio.xml
    Table      db/ddl/*.sql (Column 은 컬럼 정의에서 자동 생성)

엣지 근거:
    Screen -CALLS-> Controller   js 의 tos.ajax('/x.do') 주소 = Controller 의 클래스 + 메서드 매핑
    Screen -OPENS-> Screen       location.href / tos.popup 주소가 화면(view)을 반환하는 매핑
    Controller -INVOKES-> Service, Service -INVOKES-> Service   필드 타입이 *Service 인 호출, 같은 클래스 메서드 호출
    Controller/Service -EXECUTES-> SQL   필드 타입이 *Mapper 인 호출 → 같은 이름 namespace 의 문 ID
    Batch -EXECUTES-> SQL, Batch -INVOKES-> DBIO   Job 단계, .pc 의 EXEC SQL / tmk_dbio_*(DBIO_ID)
    SQL/DBIO -READS/WRITES-> Table   SQL 본문의 테이블 (INSERT INTO/UPDATE/DELETE/MERGE 대상은 WRITES)
    Table -REFERENCES-> Table        DDL 의 FOREIGN KEY
"""

import re
from pathlib import Path

from .model import ParseResult, new_edge

# --- 공통 텍스트 처리 ---------------------------------------------------------


def strip_comments(text: str) -> str:
    """C 계열(java/js/pc) 주석을 공백으로 바꾼다. 문자열은 유지하고 위치·줄 번호는 그대로 둔다."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in text[i:j]]
            i = j
        else:
            i += 1
    return "".join(out)


def blank_strings(text: str) -> str:
    """문자열 리터럴 내용을 공백으로 바꾼다 (호출 탐지에서 문자열 속 단어 제외)."""
    return re.sub(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[0], text)


def block_end(text: str, open_idx: int) -> int:
    """text[open_idx] == '{' 의 짝 '}' 다음 위치 (text 는 주석·문자열 처리된 상태)."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def header_field(text: str, key: str) -> str:
    m = re.search(rf"^\s*\*?\s*{key}\s*:\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


# --- SQL → 테이블 -------------------------------------------------------------

WRITE_TARGET = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE|MERGE\s+INTO)\s+(\w+)", re.I)
TABLE_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)", re.I)
SQL_KEYWORDS = {"DUAL", "SELECT", "SET", "WHERE", "VALUES"}

# --- Java ----------------------------------------------------------------------

JAVA_METHOD = re.compile(
    r"^[ \t]+(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
    r"[\w<>\[\],.?\s]+?\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w\s,.]+)?\{",
    re.M,
)
JAVA_FIELD = re.compile(r"^[ \t]+private\s+(?:final\s+)?(\w+)\s+(\w+)\s*;", re.M)
MAPPING = re.compile(r"@(Get|Post|Request)Mapping\(\s*(?:value\s*=\s*)?\"([^\"]*)\"")
VIEW_RETURN = re.compile(r"return\s+\"(?!redirect:|forward:)([\w/]+)\"\s*;")
JAVA_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "throw", "synchronized", "super", "this"}

# --- JSP / JS ------------------------------------------------------------------

SCRIPT_SRC = re.compile(r"<script[^>]*src=\"[^\"]*?(/js/[\w/.-]+\.js)")
JS_URL = re.compile(r"['\"](/[\w/]+\.do)\b")
JS_FUNC = re.compile(r"(?:\b(\w+)\s*:\s*function\s*\([^)]*\)|\bfunction\s+(\w+)\s*\([^)]*\)|\$\(\s*function\s*\(\s*\))\s*\{")
JS_BIND = re.compile(r"\$\('#(\w+)'\)\.on\('(\w+)'(?:,\s*'[^']*')?,\s*function\s*\([^)]*\)\s*(\{)\s*(?:(?:self|this)\.(\w+)\s*\()?")

# --- Pro*C ---------------------------------------------------------------------

C_FUNC = re.compile(r"^(?:static\s+)?[A-Za-z_][\w \t*]*?\b(\w+)\s*\([^;{)]*\)\s*\{", re.M)
EXEC_SQL = re.compile(r"\bEXEC\s+SQL\s+(.*?);", re.S)
CURSOR = re.compile(r"DECLARE\s+(\w+)\s+CURSOR\s+FOR\b", re.I)
C_DEFINE = re.compile(r"^#define\s+(\w+)\s+\"(\w+)\"", re.M)
DBIO_CALL = re.compile(r"\btmk_dbio_\w+\(\s*(\"\w+\"|\w+)")


class TosParser:
    def __init__(self, root: Path, domain: str, docs_root: Path | None) -> None:
        self.root = root
        self.domain = domain
        self.docs_root = docs_root
        self.r = ParseResult()
        self.ids: set[str] = set()
        self.tables: set[str] = set()
        self.methods: dict[str, dict[str, list[dict]]] = {}  # class → method name → [method info]
        self.url_map: dict[str, tuple[str, str]] = {}  # url → ("view", 화면ID) | ("ctl", node_id)

    # --- 공통 -----------------------------------------------------------------

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def add_node(self, node_id: str, label: str, name: str, path: Path, line: int, props: dict) -> dict:
        node = {"node_id": node_id, "label": label, "name": name, "domain": self.domain, "source_file": self.rel(path),
                "source_line": line, "properties": props}
        self.r.nodes.append(node)
        self.ids.add(node_id)
        return node

    def add_edge(self, from_id: str, to_id: str, type_: str, path: Path, line: int, **kw) -> None:
        self.r.edges.append(new_edge(from_id, to_id, type_, f"{self.rel(path)}:{line}", **kw))

    def table_edges(self, from_id: str, sql: str, path: Path, line: int) -> None:
        # MyBatis 태그(<update id=..>, <if ..>)와 CDATA 표시만 지운다. 비교 연산자(<=, >=)는 남긴다.
        sql_u = re.sub(r"<!\[CDATA\[|\]\]>|</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>", " ", sql).upper()
        writes = {m.group(1) for m in WRITE_TARGET.finditer(sql_u)}
        for t in sorted(writes):
            if t in self.tables:
                self.add_edge(from_id, "TBL:" + t, "WRITES", path, line)
        for t in sorted(t for t in self.tables if t not in writes and re.search(rf"\b{t}\b", sql_u)):
            self.add_edge(from_id, "TBL:" + t, "READS", path, line)
        for m in TABLE_REF.finditer(sql_u):
            t = m.group(1)
            if t not in self.tables and t not in SQL_KEYWORDS and not t.startswith(("H_", ":")):
                self.r.infos.append(f"{from_id}: DDL 없는 테이블 {t} ({self.rel(path)}:{line})")

    # --- Table / Column -------------------------------------------------------

    @staticmethod
    def ddl_blocks(text: str) -> list[tuple[str, int, int]]:
        """파일 안의 CREATE TABLE 블록들 (테이블명, 시작, 끝). 한 파일에 여러 테이블이 있을 수 있다."""
        blocks = []
        for m in re.finditer(r"CREATE TABLE\s+(\w+)\s*\(", text):
            end = text.find("\n);", m.end())
            blocks.append((m.group(1), m.start(), len(text) if end < 0 else end + 3))
        return blocks

    def parse_ddl(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        blocks = self.ddl_blocks(text)
        head = re.match(r"\s*--\s*(.+)", text)
        for tname, start, end in blocks:
            tcomment = re.search(rf"COMMENT ON TABLE\s+{tname}\s+IS\s+'([^']*)'", text)
            desc = head.group(1).strip() if head and len(blocks) == 1 else ""
            if len(blocks) == 1:
                code = text.strip("\n")
            else:
                comments = [ln for ln in text.splitlines() if re.match(rf"COMMENT ON (?:TABLE\s+{tname}\s|COLUMN\s+{tname}\.)", ln)]
                code = "\n".join([text[start:end]] + ([""] + comments if comments else []))
            tid = "TBL:" + tname
            self.tables.add(tname)
            props = {"desc": desc} if desc else {}
            self.add_node(tid, "Table", tcomment.group(1) if tcomment else (desc or tname), path, line_of(text, start),
                          props | {"code": code})

            col_comments = dict(re.findall(rf"COMMENT ON COLUMN\s+{tname}\.(\w+)\s+IS\s+'([^']*)'", text))
            body = text[start:end]
            pks: set[str] = set()
            for pk in re.finditer(r"PRIMARY KEY\s*\(([^)]*)\)", body):
                pks |= {c.strip() for c in pk.group(1).split(",")}
            for cm in re.finditer(r"^[ \t]+([A-Z][A-Z0-9_]*)[ \t]+((?:VARCHAR2|VARCHAR|NUMBER|CHAR|DATE|TIMESTAMP|CLOB)(?:\([^)]*\))?)(.*)$",
                                  body, re.M):
                col, dtype, rest = cm.group(1), cm.group(2), cm.group(3)
                line = line_of(text, start + cm.start())
                cid = f"COL:{tname}.{col}"
                self.add_node(cid, "Column", col, path, line,
                              {"table": tname, "data_type": dtype, "nullable": "NOT NULL" not in rest, "pk": col in pks,
                               "comment": col_comments.get(col, "")})
                self.add_edge(tid, cid, "HAS_COLUMN", path, line)

    def parse_fks(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        for tname, start, end in self.ddl_blocks(text):
            for fk in re.finditer(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)", text[start:end]):
                via = ",".join(c.strip() for c in fk.group(1).split(","))
                self.add_edge("TBL:" + tname, "TBL:" + fk.group(2), "REFERENCES", path, line_of(text, start + fk.start()), via=via)

    # --- SQL (MyBatis) --------------------------------------------------------

    def parse_mapper(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        ns = re.search(r"<mapper\s+namespace=\"([\w.]+)\"", text)
        if not ns:
            return
        mapper = ns.group(1).rsplit(".", 1)[-1]
        frags = {m.group(1): m.group(2).strip("\n") for m in re.finditer(r"<sql\s+id=\"(\w+)\"[^>]*>(.*?)</sql>", text, re.S)}
        for m in re.finditer(r"<(select|insert|update|delete)\s+id=\"(\w+)\"[^>]*>.*?</\1>", text, re.S):
            stmt = m.group(0)
            expanded = re.sub(r"<include\s+refid=\"(\w+)\"\s*/>", lambda x: frags.get(x.group(1), x.group(0)), stmt)
            prev = text[:m.start()].rstrip()
            desc = ""
            if prev.endswith("-->"):
                desc = re.sub(r"\s+", " ", prev[prev.rfind("<!--") + 4:-3]).strip()
            nid = f"SQL:{mapper}.{m.group(2)}"
            line = line_of(text, m.start())
            props = {"program": mapper, "stmt_type": m.group(1), "code": expanded}
            if desc:
                props["desc"] = desc
            self.add_node(nid, "SQL", f"{mapper}.{m.group(2)}", path, line, props)
            self.table_edges(nid, expanded, path, line)

    # --- DBIO -----------------------------------------------------------------

    def parse_dbio(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        m = re.search(r"<dbio\s+id=\"(\w+)\"\s+name=\"([^\"]*)\"(?:\s+type=\"(\w+)\")?", text)
        if not m:
            return
        nid = "DBIO:" + m.group(1)
        line = line_of(text, m.start())
        props = {"desc": m.group(2), "dbio_type": m.group(3) or "", "used_by": header_field(text, "사용"), "code": text.strip("\n")}
        self.add_node(nid, "DBIO", m.group(1), path, line, props)
        sql = re.search(r"<sql>(.*?)</sql>", text, re.S)
        if sql:
            self.table_edges(nid, sql.group(1), path, line)

    # --- Java (Controller / Service) -----------------------------------------

    def scan_java(self, path: Path) -> dict | None:
        text = path.read_text(encoding="utf-8")
        clean = strip_comments(text)
        cls = re.search(r"\b(?:class|interface)\s+(\w+)", clean)
        if not cls or "interface" in cls.group(0):
            return None
        name = cls.group(1)
        kind = "Controller" if name.endswith("Controller") else "Service" if name.endswith("Service") else None
        if kind is None:
            return None
        base = ""
        cm = re.search(r"@RequestMapping\(\s*\"([^\"]*)\"\s*\)\s*(?:@\w+\s*)*(?:public\s+)?class", clean)
        if cm:
            base = cm.group(1)
        fields = {fname: ftype for ftype, fname in JAVA_FIELD.findall(clean)}
        info = {"path": path, "text": text, "clean": clean, "class": name, "kind": kind, "fields": fields, "methods": []}
        prev_end = cls.end()
        for mm in JAVA_METHOD.finditer(clean):
            if mm.group(1) in JAVA_KEYWORDS:
                continue
            body_open = mm.end() - 1
            end = block_end(clean, body_open)
            between = clean[prev_end:mm.start()]
            ann = MAPPING.search(between)
            start = prev_end + between.rfind("@", 0, ann.start() + 1) if ann else mm.start()
            doc = re.search(r"/\*\*(.*?)\*/\s*$", text[prev_end:mm.start()].split("@")[0] if ann else text[prev_end:mm.start()], re.S)
            params = self.java_params(mm.group(2))
            meth = {"name": mm.group(1), "params": params, "start": start, "end": end, "body": clean[body_open:end],
                    "body_start": body_open,
                    "line": line_of(text, start), "url": base + ann.group(2) if ann else None,
                    "http": ann.group(1).upper() if ann else None,
                    "desc": re.sub(r"\s*\*\s*", " ", doc.group(1)).strip() if doc else ""}
            view = VIEW_RETURN.search(meth["body"])
            meth["view"] = view.group(1).rsplit("/", 1)[-1] if (view and ann) else None
            info["methods"].append(meth)
            prev_end = end
        counts: dict[str, int] = {}
        for meth in info["methods"]:
            counts[meth["name"]] = counts.get(meth["name"], 0) + 1
        prefix = "CTL" if kind == "Controller" else "SVC"
        for meth in info["methods"]:
            key = meth["name"] + (f"({','.join(t for t, _ in meth['params'])})" if counts[meth["name"]] > 1 else "")
            meth["id"] = f"{prefix}:{name}.{key}"
        self.methods[name] = {}
        for meth in info["methods"]:
            self.methods[name].setdefault(meth["name"], []).append(meth)
        return info

    @staticmethod
    def java_params(raw: str) -> list[tuple[str, str]]:
        params = []
        for p in filter(None, (x.strip() for x in raw.split(","))):
            p = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", p).replace("final ", "").strip()
            parts = p.rsplit(None, 1)
            if len(parts) == 2:
                params.append((re.sub(r"<.*>", "", parts[0]).split(".")[-1], parts[1]))
        return params

    def resolve_method(self, cls: str, name: str, args_raw: str, caller: dict, info: dict) -> list[str]:
        cands = self.methods.get(cls, {}).get(name, [])
        if len(cands) <= 1:
            return [c["id"] for c in cands]
        args = [a.strip() for a in args_raw.split(",")] if args_raw.strip() else []
        cands = [c for c in cands if len(c["params"]) == len(args)]
        local_types = dict((n, t) for t, n in caller["params"])
        for t, n in re.findall(r"\b([A-Z]\w*)(?:<[^>]*>)?\s+(\w+)\s*[=;]", caller["body"]):
            local_types.setdefault(n, t)
        arg_types = [local_types.get(a) for a in args]
        exact = [c for c in cands if all(at is None or at == pt for at, (pt, _) in zip(arg_types, c["params"]))]
        if len(exact) != 1:
            self.r.errors.append(f"오버로딩 판별 실패 {cls}.{name}({args_raw}) ({self.rel(info['path'])}:{caller['line']})")
            return [c["id"] for c in (exact or cands)]
        return [exact[0]["id"]]

    def link_java(self, info: dict, mapper_stmts: set[str]) -> None:
        path, kind = info["path"], info["kind"]
        for meth in info["methods"]:
            if kind == "Controller" and (meth["url"] is None or meth["view"]):
                if meth["view"]:
                    self.url_map[meth["url"]] = ("view", meth["view"])
                continue
            props = {"program": info["class"]}
            if meth["url"]:
                props.update({"url": meth["url"], "http_method": meth["http"]})
            if meth["desc"]:
                props["desc"] = meth["desc"]
            props["code"] = info["text"][meth["start"]:meth["end"]]
            self.add_node(meth["id"], kind, meth["id"].split(":", 1)[1], path, meth["line"], props)
            if meth["url"]:
                self.url_map[meth["url"]] = ("ctl", meth["id"])

            body = blank_strings(meth["body"])
            for cm in re.finditer(r"\b(\w+)\.(\w+)\s*\(", body):
                ftype = info["fields"].get(cm.group(1))
                if not ftype:
                    continue
                call_line = line_of(info["clean"], meth["body_start"] + cm.start())
                args = self.call_args(body, cm.end() - 1)
                if ftype.endswith("Mapper"):
                    sid = f"SQL:{ftype}.{cm.group(2)}"
                    if sid not in mapper_stmts:
                        self.r.errors.append(f"매퍼 문 없음 {sid} ({self.rel(path)}:{call_line})")
                    self.add_edge(meth["id"], sid, "EXECUTES", path, call_line)
                elif ftype.endswith("Service"):
                    for tid in self.resolve_method(ftype, cm.group(2), args, meth, info):
                        self.add_edge(meth["id"], tid, "INVOKES", path, call_line)
            if kind == "Service":
                for cm in re.finditer(r"(?<![\w.])(?:this\.)?(\w+)\s*\(", body):
                    if cm.group(1) in self.methods[info["class"]] and cm.group(1) not in JAVA_KEYWORDS:
                        call_line = line_of(info["clean"], meth["body_start"] + cm.start())
                        args = self.call_args(body, cm.end() - 1)
                        for tid in self.resolve_method(info["class"], cm.group(1), args, meth, info):
                            self.add_edge(meth["id"], tid, "INVOKES", path, call_line)

    @staticmethod
    def call_args(text: str, open_idx: int) -> str:
        depth = 0
        for i in range(open_idx, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1:i]
        return ""

    # --- Screen ---------------------------------------------------------------

    @staticmethod
    def js_functions(clean: str) -> list[tuple[int, int, str]]:
        """이름 있는 함수, $(function(){..}) (onload), 이벤트 핸들러 익명 함수 (<요소ID>.<이벤트>) 의 범위."""
        spans = []
        for m in JS_FUNC.finditer(clean):
            name = m.group(1) or m.group(2) or "onload"
            spans.append((m.start(), block_end(clean, m.end() - 1), name))
        for m in JS_BIND.finditer(clean):
            spans.append((m.start(3), block_end(clean, m.start(3)), f"{m.group(1)}.{m.group(2)}"))
        return spans

    @staticmethod
    def enclosing(spans: list[tuple[int, int, str]], idx: int) -> str:
        inner = [s for s in spans if s[0] <= idx < s[1]]
        return min(inner, key=lambda s: s[1] - s[0])[2] if inner else ""

    def parse_screens(self, webapp: Path) -> None:
        js_root = webapp
        common_funcs: dict[str, list[tuple[str, int, Path]]] = {}  # 공통 js 함수명 → [(url, line, path)]
        for js in sorted((webapp / "js" / "common").glob("*.js")) if (webapp / "js" / "common").is_dir() else []:
            text = js.read_text(encoding="utf-8")
            clean = strip_comments(text)
            spans = self.js_functions(clean)
            for um in JS_URL.finditer(clean):
                fn = self.enclosing(spans, um.start())
                if fn:
                    common_funcs.setdefault(fn, []).append((um.group(1), line_of(text, um.start()), js))

        for jsp in sorted((webapp / "WEB-INF" / "jsp").rglob("*.jsp")):
            sid = jsp.stem
            if not re.fullmatch(r"[A-Z]{3}[A-Z]{2}\d{4}[A-Z]", sid):
                continue
            jsp_text = jsp.read_text(encoding="utf-8")
            sources = [(jsp, jsp_text)]
            includes = SCRIPT_SRC.findall(jsp_text)
            for inc in includes:
                js = js_root / inc.lstrip("/")
                if js.is_file() and "/common/" not in inc:
                    sources.append((js, js.read_text(encoding="utf-8")))
            code = jsp_text.strip("\n")
            for js, text in sources[1:]:
                code += f"\n\n// ---- {self.rel(js)} ----\n" + text.strip("\n")
            props = {"desc": header_field(jsp_text, "설명"), "events": [], "code": code}
            if not props["desc"]:
                del props["desc"]
            node = self.add_node("SCR:" + sid, "Screen", header_field(jsp_text, "화면명") or sid, jsp, 1, props)

            uses_common = any("/common/" in inc for inc in includes)
            for path, text in sources:
                clean = strip_comments(text)
                spans = self.js_functions(clean)
                for bm in JS_BIND.finditer(clean):
                    ev = f"{bm.group(1)}.{bm.group(2)}" + (f": {bm.group(4)}" if bm.group(4) else "")
                    if ev not in node["properties"]["events"]:
                        node["properties"]["events"].append(ev)
                for um in JS_URL.finditer(clean):
                    self.link_url(sid, um.group(1), self.enclosing(spans, um.start()), path, line_of(text, um.start()))
                if uses_common:
                    for fn, targets in common_funcs.items():
                        for cm in re.finditer(rf"(?<![\w.]){fn}\s*\(", clean):
                            for url, _, _ in targets:
                                self.link_url(sid, url, self.enclosing(spans, cm.start()), path, line_of(text, cm.start()))

    def link_url(self, sid: str, url: str, event: str, path: Path, line: int) -> None:
        target = self.url_map.get(url)
        if target is None:
            self.r.infos.append(f"SCR:{sid}: 매핑 없는 주소 {url} ({self.rel(path)}:{line})")
        elif target[0] == "view":
            if target[1] != sid:
                self.add_edge("SCR:" + sid, "SCR:" + target[1], "OPENS", path, line, event=event)
        else:
            self.add_edge("SCR:" + sid, target[1], "CALLS", path, line, event=event)

    # --- Batch ----------------------------------------------------------------

    def interface_title(self, if_id: str) -> str:
        if self.docs_root:
            doc = self.docs_root / "interface" / f"{if_id}.md"
            if doc.is_file():
                m = re.search(r"^#\s+(.+)$", doc.read_text(encoding="utf-8"), re.M)
                if m:
                    title = m.group(1).strip()
                    return title.split(if_id, 1)[1].strip(" :-") if if_id in title else title
        return ""

    def parse_job(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        jm = re.search(r"<job\s+id=\"(\w+)\"\s+name=\"([^\"]*)\"(?:\s+group=\"([^\"]*)\")?(?:\s+schedule=\"([^\"]*)\")?", text)
        if not jm:
            return
        job, bid = jm.group(1), "BAT:" + jm.group(1)
        props: dict = {"cycle": header_field(text, "주기"), "cron": jm.group(4) or "", "group": jm.group(3) or "",
                       "steps": [], "interfaces": []}
        node = self.add_node(bid, "Batch", jm.group(2), path, line_of(text, jm.start()), props)
        code = text.strip("\n")
        programs = []
        for sm in re.finditer(r"<step\s+id=\"(\w+)\"\s+name=\"([^\"]*)\"\s+type=\"(\w+)\"[^>]*>(.*?)</step>", text, re.S):
            step, sname, stype, body = sm.groups()
            sline = line_of(text, sm.start())
            prog = re.search(r"<program>(\w+)</program>", body)
            dbio = re.search(r"<dbio>(\w+)</dbio>", body)
            if_id = re.search(r"<ifId>([\w-]+)</ifId>", body)
            props["steps"].append(f"{step} {sname} ({stype}{' ' + (prog or dbio or if_id).group(1) if (prog or dbio or if_id) else ''})")
            if if_id:
                title = self.interface_title(if_id.group(1))
                props["interfaces"].append(if_id.group(1) + (f" {title}" if title else ""))
            if dbio:
                self.add_edge(bid, "DBIO:" + dbio.group(1), "INVOKES", path, sline, note=step)
            if stype == "SQL":
                sql = re.search(r"<sql>(.*?)</sql>", body, re.S)
                if sql:
                    qid = f"SQL:{job}.{step}"
                    self.add_node(qid, "SQL", f"{job}.{step}", path, sline,
                                  {"program": job, "stmt_type": sql.group(1).split()[0].lower(), "desc": sname,
                                   "code": sql.group(1).strip()})
                    self.add_edge(bid, qid, "EXECUTES", path, sline, note=step)
                    self.table_edges(qid, sql.group(1), path, sline)
            if prog:
                pc = self.root / "batch" / "proc" / f"{prog.group(1)}.pc"
                if not pc.is_file():
                    self.r.infos.append(f"{bid}: {step} 프로그램 {prog.group(1)}.pc 없음 (적재 범위 밖)")
                    continue
                programs.append(prog.group(1))
                pc_text = pc.read_text(encoding="utf-8")
                code += f"\n\n// ---- {self.rel(pc)} ----\n" + pc_text.strip("\n")
                self.parse_pc(bid, prog.group(1), pc, pc_text, step)
        props["program"] = ",".join(programs)
        if header_field(text, "Job명") not in ("", jm.group(2)):
            props["desc"] = header_field(text, "Job명")
        if not props["interfaces"]:
            del props["interfaces"]
        node["properties"] = {k: v for k, v in props.items() if v != ""} | {"code": code}

    def parse_pc(self, bid: str, prog: str, path: Path, text: str, step: str) -> None:
        clean = strip_comments(text)
        funcs = [(m.start(), block_end(clean, m.end() - 1), m.group(1)) for m in C_FUNC.finditer(clean)
                 if m.group(1) not in JAVA_KEYWORDS]
        per_func: dict[str, int] = {}
        for em in EXEC_SQL.finditer(clean):
            stmt = em.group(1).strip()
            first = stmt.split(None, 1)[0].upper() if stmt else ""
            cur = CURSOR.match(stmt)
            if not cur and first not in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
                continue
            func = self.enclosing(funcs, em.start()) or "main"
            if cur:
                key = cur.group(1)
            else:
                per_func[func] = per_func.get(func, 0) + 1
                key = func + (f"#{per_func[func]}" if per_func[func] > 1 else "")
            qid = f"SQL:{prog}.{key}"
            line = line_of(text, em.start())
            sql_text = text[em.start():em.end()]
            stype = "cursor" if cur else first.lower()
            self.add_node(qid, "SQL", f"{prog}.{key}", path, line,
                          {"program": prog, "function": func, "stmt_type": stype, "code": sql_text})
            self.add_edge(bid, qid, "EXECUTES", path, line, note=f"{step} {prog}.{func}")
            self.table_edges(qid, stmt, path, line)

        macros = dict(C_DEFINE.findall(clean))
        for dm in DBIO_CALL.finditer(clean):
            arg = dm.group(1).strip('"')
            dbio = macros.get(arg, arg)
            if "DBIO:" + dbio in self.ids:
                func = self.enclosing(funcs, dm.start()) or "main"
                self.add_edge(bid, "DBIO:" + dbio, "INVOKES", path, line_of(text, dm.start()), note=f"{step} {prog}.{func}")

    # --- 실행 -----------------------------------------------------------------

    def run(self) -> ParseResult:
        ddls = sorted((self.root / "db").rglob("*.sql")) if (self.root / "db").is_dir() else []
        for p in ddls:
            self.parse_ddl(p)
        for p in ddls:
            self.parse_fks(p)
        online = self.root / "online"
        for p in sorted((online / "resources").rglob("*.xml")) if online.is_dir() else []:
            self.parse_mapper(p)
        mapper_stmts = {n["node_id"] for n in self.r.nodes if n["label"] == "SQL"}
        for p in sorted((self.root / "batch" / "dbio").glob("*.dbio.xml")):
            self.parse_dbio(p)

        infos = [i for p in sorted((online / "java").rglob("*.java")) if (i := self.scan_java(p))]
        for info in sorted(infos, key=lambda i: i["kind"] != "Service"):
            self.link_java(info, mapper_stmts)
        if (online / "webapp").is_dir():
            self.parse_screens(online / "webapp")
        for p in sorted((self.root / "batch" / "job").glob("*.xml")):
            self.parse_job(p)
        return self.r


def parse(root: Path, domain: str = "tos") -> ParseResult:
    docs = root.parents[1] / "docs" / domain
    return TosParser(root, domain, docs if docs.is_dir() else None).run()
