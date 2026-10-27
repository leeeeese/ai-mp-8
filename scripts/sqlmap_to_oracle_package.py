"""swing 목업 SQL 맵(@member 어노테이션 + MyBatis #{...})을 Oracle 패키지 .sql 로 바꾼다 (주석 없는 실제 SQL).

- 패키지 = 문장 ID 의 앞부분 (DBIO:CombSql.selectCombGrp → 패키지 CombSql), 패키지마다 <패키지>.sql 한 파일 (spec + body)
- SELECT/WITH → FUNCTION ... RETURN SYS_REFCURSOR (OPEN c FOR ...), INSERT/UPDATE/DELETE/MERGE → PROCEDURE
- #{camelCase} → p_snake_case 파라미터. 타입은 테이블 DDL 의 컬럼 타입으로 추정 (없으면 이름으로: *Dtm TIMESTAMP, *Amt·*Cnt NUMBER, 그 외 VARCHAR2)
- MyBatis 동적 SQL
    <if test="x != null"> AND <조건> </if>   →  AND (p_x IS NULL OR <조건>)
    <if test="x == 'v'"> , <컬럼> </if>     →  , CASE WHEN p_x = 'v' THEN <컬럼> END AS <컬럼명>
- 주석·어노테이션은 모두 버린다

usage: python scripts/sqlmap_to_oracle_package.py <어노테이션 SQL 폴더> <테이블 DDL 폴더> <출력 폴더>
"""

import re
import sys
from pathlib import Path

ANNOTATION = re.compile(r"^\s*--\s*@(\w+)(?:\s+(.*?))?\s*$")
COLUMN_LINE = re.compile(r"^\s+([A-Z][A-Z0-9_]*)\s+(VARCHAR2?|NUMBER|CHAR|TIMESTAMP|CLOB|DATE)\b")
PARAM = re.compile(r"#\{(\w+)\}")
IF_OPEN = re.compile(r'^\s*<if\s+test="([^"]+)">\s*$')
IF_CLOSE = re.compile(r"^\s*</if>\s*$")
TEST_NOT_NULL = re.compile(r"^(\w+)\s*!=\s*null$")
TEST_EQUALS = re.compile(r"^(\w+)\s*==\s*'([^']*)'$")
PLSQL_TYPE = {"VARCHAR": "VARCHAR2", "VARCHAR2": "VARCHAR2", "CHAR": "CHAR", "NUMBER": "NUMBER", "TIMESTAMP": "TIMESTAMP",
              "CLOB": "CLOB", "DATE": "DATE"}


def snake(name: str) -> str:
    return "p_" + re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def strip_comment(line: str) -> str:
    """따옴표 밖 -- 이후를 버린다."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            quote = None if ch == quote else quote
        elif ch in ("'", '"'):
            quote = ch
        elif line.startswith("--", i):
            return line[:i].rstrip()
    return line.rstrip()


def load_column_types(ddl_dir: Path) -> dict[str, str]:
    types: dict[str, str] = {}
    for path in sorted(ddl_dir.glob("*.sql")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := COLUMN_LINE.match(line):
                types.setdefault(m.group(1), PLSQL_TYPE[m.group(2)])
    return types


def split_top(text: str) -> list[str]:
    """괄호 밖 쉼표로 나눈다."""
    parts, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return parts


def param_columns(sql: str) -> dict[str, str]:
    """파라미터 → 비교·대입되는 컬럼."""
    cols: dict[str, str] = {}
    for col, a, b in re.findall(r"(?:\w+\.)?([A-Z][A-Z0-9_]*)\s+BETWEEN\s+#\{(\w+)\}\s+AND\s+#\{(\w+)\}", sql):
        cols.setdefault(a, col)
        cols.setdefault(b, col)
    for col, p in re.findall(r"(?:\w+\.)?([A-Z][A-Z0-9_]*)\s*(?:=|<>|>=|<=|<|>|\bLIKE\b)\s*#\{(\w+)\}", sql):
        cols.setdefault(p, col)
    for p, col in re.findall(r"#\{(\w+)\}\s+AS\s+([A-Z][A-Z0-9_]*)", sql):
        cols.setdefault(p, col)
    for col_list, val_list in re.findall(r"INSERT\s*(?:INTO\s+\w+\s*)?\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*;?\s*$", sql, re.DOTALL | re.MULTILINE):
        for col, val in zip(split_top(col_list), split_top(val_list)):
            if m := re.fullmatch(r"#\{(\w+)\}", val):
                cols.setdefault(m.group(1), col)
    return cols


def guess_type(param: str, column: str | None, types: dict[str, str]) -> str:
    if column and column in types:
        return types[column]
    if re.search(r"(Dtm|Dt)$", param):
        return "TIMESTAMP" if param.endswith("Dtm") else "VARCHAR2"
    if re.search(r"(Amt|Cnt|Qty|Seq|Rate)$", param):
        return "NUMBER"
    return "VARCHAR2"


def convert_dynamic(lines: list[str], stmt_id: str) -> list[str]:
    """<if> 블록을 일반 SQL 로 바꾼다."""
    out, i = [], 0
    while i < len(lines):
        m = IF_OPEN.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        body = []
        i += 1
        while not IF_CLOSE.match(lines[i]):
            body.append(lines[i])
            i += 1
        i += 1
        if len(body) != 1:
            raise ValueError(f"{stmt_id}: <if> 본문은 한 줄만 지원합니다: {body}")
        line = body[0]
        indent = line[: len(line) - len(line.lstrip())]
        text = line.strip()
        test = m.group(1).strip()
        if (nn := TEST_NOT_NULL.match(test)) and re.match(r"^(AND|OR)\s+", text, re.IGNORECASE):
            op, cond = text.split(None, 1)
            out.append(f"{indent}{op} ({snake(nn.group(1))} IS NULL OR {cond})")
        elif (eq := TEST_EQUALS.match(test)) and text.startswith(","):
            col = text[1:].strip()
            out.append(f"{indent}, CASE WHEN {snake(eq.group(1))} = '{eq.group(2)}' THEN {col} END AS {col.split('.')[-1]}")
        else:
            raise ValueError(f"{stmt_id}: 지원하지 않는 <if> 형태: test={test!r} body={text!r}")
    return out


def parse_sqlmap(path: Path) -> list[tuple[str, str, list[str]]]:
    """(패키지, 문장 이름, SQL 줄) 목록."""
    stmts, cur, buf = [], None, []
    for raw in path.read_text(encoding="utf-8").splitlines():
        ann = ANNOTATION.match(raw)
        if ann:
            if ann.group(1) == "member" and ann.group(2) and ann.group(2).startswith("DBIO "):
                cur = ann.group(2).split()[1].split(":", 1)[1]
                buf = []
            continue
        if cur is None:
            continue
        line = strip_comment(raw)
        if not line.strip():
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            pkg, name = cur.split(".", 1)
            stmts.append((pkg, name, buf))
            cur, buf = None, []
    if cur is not None:
        raise ValueError(f"{path}: {cur} 문장이 ; 로 끝나지 않습니다")
    return stmts


def render_routine(name: str, sql_lines: list[str], types: dict[str, str]) -> tuple[str, str]:
    raw_sql = "\n".join(sql_lines)
    params = list(dict.fromkeys(PARAM.findall(raw_sql) + [t for t in re.findall(r'test="(\w+)', raw_sql)]))
    cols = param_columns(raw_sql)
    body = convert_dynamic(sql_lines, name)
    body = [PARAM.sub(lambda m: snake(m.group(1)), line) for line in body]
    body[-1] = body[-1].rstrip().rstrip(";")
    base = min(len(x) - len(x.lstrip()) for x in body)
    is_query = body[0].lstrip().upper().startswith(("SELECT", "WITH"))
    pad = " " * (6 if is_query else 4)
    sql = "\n".join(pad + x[base:] for x in body) + ";"
    sig = ""
    if params:
        width = max(len(snake(p)) for p in params)
        args = ",\n".join(f"    {snake(p):<{width}} IN {guess_type(p, cols.get(p), types)}" for p in params)
        sig = f"(\n{args}\n  )"
    if is_query:
        spec = f"  FUNCTION {name}{sig} RETURN SYS_REFCURSOR"
        impl = f"{spec} IS\n    c SYS_REFCURSOR;\n  BEGIN\n    OPEN c FOR\n{sql}\n    RETURN c;\n  END {name};"
    else:
        spec = f"  PROCEDURE {name}{sig}"
        impl = f"{spec} IS\n  BEGIN\n{sql}\n  END {name};"
    return spec + ";", impl


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    src, ddl, dst = (Path(a) for a in sys.argv[1:])
    if dst.exists() and any(dst.iterdir()):
        print(f"출력 폴더가 비어 있지 않습니다: {dst}")
        return 1
    dst.mkdir(parents=True, exist_ok=True)
    types = load_column_types(ddl)
    packages: dict[str, list[tuple[str, list[str]]]] = {}
    for path in sorted(src.glob("*.sql")):
        for pkg, name, lines in parse_sqlmap(path):
            packages.setdefault(pkg, []).append((name, lines))
    total = 0
    for pkg, routines in packages.items():
        rendered = [render_routine(name, lines, types) for name, lines in routines]
        spec = "\n\n".join(s for s, _ in rendered)
        body = "\n\n".join(b for _, b in rendered)
        text = (f"CREATE OR REPLACE PACKAGE {pkg} AS\n\n{spec}\n\nEND {pkg};\n/\n\n"
                f"CREATE OR REPLACE PACKAGE BODY {pkg} AS\n\n{body}\n\nEND {pkg};\n/\n")
        (dst / f"{pkg}.sql").write_text(text, encoding="utf-8")
        total += len(routines)
    print(f"패키지 {len(packages)}개, 프로시저·함수 {total}개 → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
