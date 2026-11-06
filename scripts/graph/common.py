"""공통 파서 뼈대: 영역별 파서(parse_<domain>.py)가 함께 쓰는 소스 해석 도구 (언어·영역과 무관한 부분).

영역별 파서는 BaseParser 를 상속하고 "그 영역 언어를 읽는 부분"만 구현한다. 출력·검증·적재는 model.py.

- BaseParser: 노드·관계 추가, SQL 본문 → 테이블 관계
- 텍스트: 줄 번호
- DDL: CREATE TABLE 블록, 컬럼 정의, PK
- SQL → 테이블: 쓰기 대상(INSERT INTO/UPDATE/DELETE/MERGE)과 읽기 테이블
- 정의서(md): "## <키> <제목>" 절, "설명:" 줄, 마크다운 표 → 한글 이름·설명 보완 (소스에 설명이 없는 영역용)
"""

import re
from pathlib import Path

from .model import ParseResult, new_edge


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


# --- DDL ---------------------------------------------------------------------------

DDL_COLUMN = re.compile(
    r"^[ \t]+([A-Z][A-Z0-9_]*)[ \t]+((?:VARCHAR2|VARCHAR|NUMBER|CHAR|DATE|TIMESTAMP|CLOB)(?:\([^)]*\))?)(.*)$", re.M)
PRIMARY_KEY = re.compile(r"PRIMARY KEY\s*\(([^)]*)\)")


def ddl_blocks(text: str) -> list[tuple[str, int, int]]:
    """파일 안의 CREATE TABLE 블록들 (테이블명, 시작, 끝). 한 파일에 여러 테이블이 있을 수 있다."""
    blocks = []
    for m in re.finditer(r"CREATE TABLE\s+(\w+)\s*\(", text):
        end = text.find("\n);", m.end())
        blocks.append((m.group(1), m.start(), len(text) if end < 0 else end + 3))
    return blocks


def ddl_columns(text: str, start: int, end: int) -> list[dict]:
    """CREATE TABLE 블록의 컬럼 정의: [{name, data_type, nullable, pk, line}]."""
    body = text[start:end]
    pks: set[str] = set()
    for pk in PRIMARY_KEY.finditer(body):
        pks |= {c.strip() for c in pk.group(1).split(",")}
    return [{"name": m.group(1), "data_type": m.group(2), "nullable": "NOT NULL" not in m.group(3), "pk": m.group(1) in pks,
             "line": line_of(text, start + m.start())}
            for m in DDL_COLUMN.finditer(body)]


# --- SQL → 테이블 -------------------------------------------------------------------

WRITE_TARGET = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE|MERGE\s+INTO)\s+(\w+)", re.I)
TABLE_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)", re.I)
SQL_KEYWORDS = {"DUAL", "SELECT", "SET", "WHERE", "VALUES"}


def sql_tables(sql_upper: str, tables: set[str]) -> tuple[list[str], list[str], list[str]]:
    """대문자 SQL 본문에서 (쓰기 테이블, 읽기 테이블, DDL 없는 테이블 참조)."""
    writes = {m.group(1) for m in WRITE_TARGET.finditer(sql_upper)}
    write_list = sorted(t for t in writes if t in tables)
    read_list = sorted(t for t in tables if t not in writes and re.search(rf"\b{t}\b", sql_upper))
    unknown = [m.group(1) for m in TABLE_REF.finditer(sql_upper)
               if m.group(1) not in tables and m.group(1) not in SQL_KEYWORDS and not m.group(1).startswith(("H_", ":"))]
    return write_list, read_list, unknown


# --- 정의서 (md) ---------------------------------------------------------------------

SECTION = re.compile(r"^##\s+(\S+)(?:\s+(.*?))?\s*$")


def md_rows(lines: list[str]) -> list[dict]:
    """마크다운 표들을 행 dict 목록으로 (머리행 기준, 구분행 제외)."""
    rows, header = [], None
    for ln in lines:
        if not ln.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif not all(set(c) <= set("-: ") for c in cells):
            rows.append(dict(zip(header, cells)))
    return rows


def read_def_doc(path: Path) -> dict[str, dict]:
    """정의서 md → {절 키: {title, desc, rows}}. 절이 없는 앞부분 표는 키 "" 에 둔다."""
    sections: dict[str, dict] = {}
    key, title, desc, buf = "", "", "", []

    def flush() -> None:
        sections[key] = {"title": title, "desc": desc, "rows": md_rows(buf)}

    for ln in path.read_text(encoding="utf-8").splitlines():
        m = SECTION.match(ln)
        if m:
            flush()
            key, title, desc, buf = m.group(1), (m.group(2) or "").strip(), "", []
        elif ln.startswith("설명:"):
            desc = ln.split(":", 1)[1].strip()
        else:
            buf.append(ln)
    flush()
    return sections


# --- 공통 파서 ------------------------------------------------------------------------


class BaseParser:
    """영역별 파서 공통 부분. 하위 클래스는 run() 에서 영역 언어를 읽어 add_node/add_edge 를 부른다."""

    def __init__(self, root: Path, domain: str, docs_root: Path | None) -> None:
        self.root = root
        self.domain = domain
        self.docs_root = docs_root
        self.r = ParseResult()
        self.ids: set[str] = set()
        self.tables: set[str] = set()

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

    def table_edges(self, from_id: str, sql_upper: str, path: Path, line: int) -> None:
        """SQL 본문(대문자, 언어별 태그 제거 후)의 테이블 → WRITES / READS. DDL 없는 테이블은 참고 사항으로 남긴다."""
        writes, reads, unknown = sql_tables(sql_upper, self.tables)
        for t in writes:
            self.add_edge(from_id, "TBL:" + t, "WRITES", path, line)
        for t in reads:
            self.add_edge(from_id, "TBL:" + t, "READS", path, line)
        for t in unknown:
            self.r.infos.append(f"{from_id}: DDL 없는 테이블 {t} ({self.rel(path)}:{line})")

    def run(self) -> ParseResult:
        raise NotImplementedError
