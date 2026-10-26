"""swing 소스를 코드 관례대로 읽어 공통 그래프 모델 형식의 노드/엣지를 만든다 (그래프용 주석 없음).

    Screen   screen/*.scr       SCREEN <화면ID> "<화면명>" … EVENT <이벤트> … END EVENT
    TP       program/*.pgm      FUNCTION 중 화면 EVENT 가 직접 CALL 하는 함수 (화면이 부르는 서비스)
    Module   program/*.pgm      그 밖의 FUNCTION (내부·공통 로직). 배치 Job 의 실행 함수는 Batch 에 흡수
    DBIO     sql/*.sql          PACKAGE BODY <패키지> 안의 FUNCTION/PROCEDURE
    Batch    batch/*.bat        JOB <JobID> "<Job명>" · PROGRAM <프로그램>.<함수> · LAUNCHER <기동기>
    Table    table/*.sql        CREATE TABLE (Column 은 컬럼 정의에서 자동 생성)

엣지 근거:
    Screen -CALLS-> TP                 EVENT 안의 CALL <프로그램>.<함수>
    Screen -OPENS-> Screen             OPEN / POPUP <화면ID>
    TP -CALLS-> TP, 나머지 -INVOKES->   FUNCTION 안의 CALL <프로그램>.<함수> (같은 프로그램은 CALL <함수>)
    TP/Module/Batch -INVOKES-> DBIO    SQL <패키지>.<함수>
    Batch -INVOKES-> …                 Job 실행 함수의 호출 + LAUNCHER 기동기가 Job 실행 뒤 부르는 SQL (INVOKE(…) 가 있는 함수)
    DBIO -READS/WRITES-> Table         PL/SQL 본문의 테이블 (공통: common.sql_tables)

한글 이름·설명은 소스에 없으므로 정의서(DOCS_ROOT_SWING)에서 보완한다:
    table/*_table_def.md (테이블·컬럼·공통코드 그룹), program/*_program_def.md (프로그램·SQL 패키지·함수),
    screen/*_screen_def.md (화면 설명·이미지 리소스), batch/*_batch_def.md (Job 설명·주기), interface/*.if (인터페이스명)
"""

import re
from pathlib import Path

from .common import BaseParser, ddl_blocks, ddl_columns, line_of, read_def_doc
from .model import ParseResult

FUNC_BLOCK = re.compile(r"^([ \t]*)FUNCTION\s+(\w+)\s*\(.*?^\1END FUNCTION\b", re.M | re.S)
EVENT_BLOCK = re.compile(r"^([ \t]*)EVENT\s+(\w+).*?^\1END EVENT\b", re.M | re.S)
PKG_BODY = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\s+(\w+)", re.I)
PKG_MEMBER = re.compile(r"^([ \t]*)(?:FUNCTION|PROCEDURE)\s+(\w+)\b.*?^\1END\s+\2\s*;", re.M | re.S)
CALL = re.compile(r"\bCALL\s+(?:(\w+)\.)?(\w+)\s*\(")
SQL_CALL = re.compile(r"\bSQL\s+(\w+)\.(\w+)\s*\(")
OPEN_SCREEN = re.compile(r"\b(?:OPEN|POPUP)\s+([A-Z]{4}\w+)\s*\(")
IF_USE = re.compile(r"IfClient\.send\(\s*'(\w+)'|IF_ENDPOINT\[\s*'(\w+)'\s*\]")
IF_DEF = re.compile(r'^INTERFACE\s+(\w+)\s+"([^"]*)"', re.M)
CODE_ROW = re.compile(r"INSERT INTO TB_COMM_CD_DTL\s*\([^)]*\)\s*VALUES\s*\('(\w+)',\s*'([^']*)',\s*'([^']*)'")
CODE_REF = re.compile(r"\(([A-Z][A-Z_]*_CD)\b")


class SwingParser(BaseParser):
    """swing 언어(SCREEN · PROGRAM · PL/SQL PACKAGE · JOB · DDL) 읽기. 공통 부분은 BaseParser."""

    def __init__(self, root: Path, domain: str, docs_root: Path | None) -> None:
        super().__init__(root, domain, docs_root)
        self.funcs: dict[str, dict] = {}  # "<프로그램>.<함수>" → {path, line, code, calls, sqls, ifs, dynamic}
        self.screens: list[dict] = []
        self.jobs: list[dict] = []
        self.docs = self.load_docs()

    # --- 정의서 ---------------------------------------------------------------

    def load_docs(self) -> dict:
        docs: dict = {"table": {}, "program": {}, "screen": {}, "batch": {}, "interface": {}}
        if not self.docs_root:
            return docs
        for kind in ("table", "program", "screen", "batch"):
            for path in sorted((self.docs_root / kind).glob("*.md")):
                docs[kind].update(read_def_doc(path))
        for path in sorted((self.docs_root / "interface").glob("*")):
            docs["interface"].update(IF_DEF.findall(path.read_text(encoding="utf-8")))
        return docs

    def doc_row(self, kind: str, key_col: str, key: str) -> dict:
        """정의서 목록 표("" 절 또는 첫 절)에서 key 행."""
        for sec in self.docs[kind].values():
            for row in sec["rows"]:
                if row.get(key_col) == key:
                    return row
        return {}

    # --- 읽기 -----------------------------------------------------------------

    def read_codes(self) -> dict[str, dict]:
        groups = {r["그룹코드"]: r["그룹명"] for sec in self.docs["table"].values() for r in sec["rows"] if "그룹코드" in r}
        codes: dict[str, dict] = {}
        for path in sorted((self.root / "code").glob("*.sql")):
            for m in CODE_ROW.finditer(path.read_text(encoding="utf-8")):
                codes.setdefault(m.group(1), {"name": groups.get(m.group(1), ""), "values": []})["values"].append((m.group(2), m.group(3)))
        return codes

    def parse_tables(self, codes: dict) -> None:
        for path in sorted((self.root / "table").glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            for tname, start, end in ddl_blocks(text):
                sec = self.docs["table"].get(tname, {})
                tid = "TBL:" + tname
                self.tables.add(tname)
                props = {"desc": sec["desc"]} if sec.get("desc") else {}
                self.add_node(tid, "Table", sec.get("title") or tname, path, line_of(text, start), props | {"code": text[start:end]})
                comments = {r["컬럼"]: r.get("설명", "") for r in sec.get("rows", []) if "컬럼" in r}
                for c in ddl_columns(text, start, end):
                    comment = comments.get(c["name"], "")
                    props = {"table": tname, "data_type": c["data_type"], "nullable": c["nullable"], "pk": c["pk"], "comment": comment}
                    ref = CODE_REF.search(comment)
                    grp = ref.group(1) if ref else (c["name"] if c["name"] in codes else None)
                    if grp and grp in codes:
                        props |= {"code_grp": grp, "code_grp_nm": codes[grp]["name"],
                                  "code_values": ", ".join(f"{k}={v}" for k, v in codes[grp]["values"])}
                    cid = f"COL:{tname}.{c['name']}"
                    self.add_node(cid, "Column", c["name"], path, c["line"], props)
                    self.add_edge(tid, cid, "HAS_COLUMN", path, c["line"])

    def parse_packages(self) -> None:
        for path in sorted((self.root / "sql").glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            body = PKG_BODY.search(text)
            if not body:
                continue
            pkg = body.group(1)
            prog = self.doc_row("program", "이름", pkg)
            fdesc = {r["함수"]: r.get("설명", "") for r in self.docs["program"].get(pkg, {}).get("rows", []) if "함수" in r}
            for m in PKG_MEMBER.finditer(text, body.end()):
                nid = f"DBIO:{pkg}.{m.group(2)}"
                line = line_of(text, m.start())
                props = {"program": pkg, "program_name": prog.get("한글명", ""), "code": m.group(0)}
                if fdesc.get(m.group(2)):
                    props["desc"] = fdesc[m.group(2)]
                self.add_node(nid, "DBIO", f"{pkg}.{m.group(2)}", path, line, props)
                self.table_edges(nid, m.group(0).upper(), path, line)

    def scan_programs(self) -> None:
        for path in sorted((self.root / "program").glob("*.pgm")):
            text = path.read_text(encoding="utf-8")
            prog = re.search(r"^PROGRAM\s+(\w+)", text, re.M).group(1)
            for m in FUNC_BLOCK.finditer(text):
                code = m.group(0)
                self.funcs[f"{prog}.{m.group(2)}"] = {
                    "program": prog, "func": m.group(2), "path": path, "line": line_of(text, m.start()), "code": code,
                    "calls": [(f"{c.group(1) or prog}.{c.group(2)}", line_of(text, m.start() + c.start())) for c in CALL.finditer(code)],
                    "sqls": [(f"{s.group(1)}.{s.group(2)}", line_of(text, m.start() + s.start())) for s in SQL_CALL.finditer(code)],
                    "ifs": [a or b for a, b in IF_USE.findall(code)],
                    "dynamic": "INVOKE(" in code}

    def scan_screens(self) -> None:
        for path in sorted((self.root / "screen").glob("*.scr")):
            text = path.read_text(encoding="utf-8")
            head = re.search(r'^SCREEN\s+(\w+)\s+"([^"]*)"', text, re.M)
            events = []
            for ev in EVENT_BLOCK.finditer(text):
                events.append({"name": ev.group(2),
                               "calls": [(f"{c.group(1)}.{c.group(2)}", line_of(text, ev.start() + c.start()))
                                         for c in CALL.finditer(ev.group(0)) if c.group(1)]})
            opens = [(o.group(1), line_of(text, o.start())) for o in OPEN_SCREEN.finditer(text)]
            self.screens.append({"id": head.group(1), "name": head.group(2), "path": path, "line": line_of(text, head.start()),
                                 "text": text, "events": events, "opens": opens})

    def scan_jobs(self) -> None:
        for path in sorted((self.root / "batch").glob("*.bat")):
            text = path.read_text(encoding="utf-8")
            head = re.search(r'^JOB\s+(\w+)\s+"([^"]*)"', text, re.M)
            pgm = re.search(r"^\s*PROGRAM\s+([\w.]+)", text, re.M)
            launcher = re.search(r"^\s*LAUNCHER\s+(\w+)", text, re.M)
            cron = re.search(r"CRON\s+'([^']*)'", text)
            self.jobs.append({"id": head.group(1), "name": head.group(2), "path": path, "line": line_of(text, head.start()),
                              "text": text, "program": pgm.group(1) if pgm else "", "launcher": launcher.group(1) if launcher else "",
                              "cron": cron.group(1) if cron else ""})

    # --- 노드·엣지 ---------------------------------------------------------------

    def interfaces(self, if_ids: list[str]) -> list[str]:
        return [f"{i} {self.docs['interface'].get(i, '')}".strip() for i in dict.fromkeys(if_ids)]

    def run(self) -> ParseResult:
        self.parse_tables(self.read_codes())
        self.parse_packages()
        self.scan_programs()
        self.scan_screens()
        self.scan_jobs()

        # 함수 라벨: 화면이 직접 부르면 TP, 아니면 Module. Job 실행 함수는 Batch 가 흡수
        job_funcs = {j["program"] for j in self.jobs}
        screen_called = {c for s in self.screens for ev in s["events"] for c, _ in ev["calls"]}
        fid: dict[str, str] = {}
        for key, f in self.funcs.items():
            if key in job_funcs:
                continue
            label = "TP" if key in screen_called else "Module"
            fid[key] = ("TP:" if label == "TP" else "MOD:") + key
            prog = self.doc_row("program", "이름", f["program"])
            fdesc = {r["함수"]: r.get("설명", "") for r in self.docs["program"].get(f["program"], {}).get("rows", []) if "함수" in r}
            props = {"program": f["program"], "program_name": prog.get("한글명", ""), "code": f["code"]}
            if prog.get("설명"):
                props["program_desc"] = prog["설명"]
            if fdesc.get(f["func"]):
                props["desc"] = fdesc[f["func"]]
            if f["ifs"]:
                props["interfaces"] = self.interfaces(f["ifs"])
            self.add_node(fid[key], label, key, f["path"], f["line"], props)

        for s in self.screens:
            sec = self.doc_row("screen", "화면", s["id"])
            images = [f"{r['이미지']} | {r.get('내용', '')}" for r in self.docs["screen"].get(s["id"], {}).get("rows", []) if "이미지" in r]
            props = {"events": [ev["name"] for ev in s["events"]], "code": s["text"]}
            if sec.get("설명"):
                props["desc"] = sec["설명"]
            if images:
                props["images"] = images
            self.add_node("SCR:" + s["id"], "Screen", s["name"], s["path"], s["line"], props)

        launchers = {f["program"]: f for f in self.funcs.values() if f["dynamic"]}
        for j in self.jobs:
            f = self.funcs.get(j["program"])
            row = self.doc_row("batch", "Job", j["id"])
            props = {"program": j["program"].split(".")[0], "cron": j["cron"], "code": j["text"].rstrip("\n")}
            if f:  # 실행 함수 본문을 흡수 (Retriever 는 표시된 파일을 이어 붙여 읽는다)
                props["code"] += f"\n\n// ---- {self.rel(f['path'])} ----\n" + f["code"]
            if row.get("주기"):
                props["cycle"] = row["주기"]
            if row.get("설명"):
                props["desc"] = row["설명"]
            if f and f["ifs"]:
                props["interfaces"] = self.interfaces(f["ifs"])
            self.add_node("BAT:" + j["id"], "Batch", j["name"], j["path"], j["line"], props)

        # 엣지
        def link(src_id: str, src_label: str, calls, sqls, path: Path, event: str = "") -> None:
            for key, line in calls:
                if key not in fid:
                    if key not in self.funcs:
                        self.r.infos.append(f"{src_id}: 그래프 밖 호출 {key} ({self.rel(path)}:{line})")
                    continue
                dst_label = "TP" if fid[key].startswith("TP:") else "Module"
                type_ = "CALLS" if src_label == "Screen" or (src_label, dst_label) == ("TP", "TP") else "INVOKES"
                self.add_edge(src_id, fid[key], type_, path, line, event=event)
            for key, line in sqls:
                if "DBIO:" + key not in self.ids:
                    self.r.errors.append(f"{src_id}: 없는 SQL {key} ({self.rel(path)}:{line})")
                    continue
                self.add_edge(src_id, "DBIO:" + key, "INVOKES", path, line, event=event)

        for key, f in self.funcs.items():
            if key in fid:
                link(fid[key], "TP" if fid[key].startswith("TP:") else "Module", f["calls"], f["sqls"], f["path"])
        for s in self.screens:
            for ev in s["events"]:
                link("SCR:" + s["id"], "Screen", ev["calls"], [], s["path"], event=ev["name"])
            for target, line in s["opens"]:
                if target != s["id"]:
                    self.add_edge("SCR:" + s["id"], "SCR:" + target, "OPENS", s["path"], line)
        for j in self.jobs:
            f = self.funcs.get(j["program"])
            if f:
                link("BAT:" + j["id"], "Batch", f["calls"], f["sqls"], f["path"])
            launcher = launchers.get(j["launcher"])
            if launcher:  # 기동기가 Job 실행(INVOKE) 뒤 남기는 실행 결과·이력 SQL
                link("BAT:" + j["id"], "Batch", [], launcher["sqls"], launcher["path"])
        return self.r


def parse(root: Path, domain: str = "swing") -> ParseResult:
    docs = root.parents[1] / "docs" / domain
    return SwingParser(root, domain, docs if docs.is_dir() else None).run()
