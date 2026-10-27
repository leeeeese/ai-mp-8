"""목업 소스에서 주석(@ 어노테이션 포함)을 모두 지워 실제 소스코드처럼 코드만 남긴 사본을 만든다.

평가용 목업 소스의 주석·어노테이션에는 노드 ID·호출 관계·업무 규칙이 적혀 있어, Retriever 가 원문을 읽을 때
영향 경로 힌트가 LLM 에 그대로 보인다. 이 스크립트로 만든 사본을 소스 루트(SOURCE_ROOT_<DOMAIN>)로 쓴다.

확장자별 주석 (따옴표 안 문자열은 건드리지 않음)
    .bat .if                   줄 맨 앞 # , //
    .scr .pgm .java .js        // , /* */
    .sql .xml(mapper)          -- , /* */      (# 은 MyBatis 바인드 변수 #{...} 라서 주석으로 보지 않음)
주석만 있던 줄은 지우고, 연속된 빈 줄은 하나로 줄인다. 그 밖의 확장자는 그대로 복사한다.

usage: python scripts/strip_source_comments.py <원본 폴더> <출력 폴더>
"""

import shutil
import sys
from pathlib import Path

LINE_MARKERS = {
    ".bat": ["//"], ".if": ["//"],
    ".scr": ["//"], ".pgm": ["//"], ".java": ["//"], ".js": ["//"],
    ".sql": ["--"], ".xml": ["--"],
}
HASH_LINE = {".bat", ".if"}  # 줄 맨 앞 # 이 주석인 확장자
BLOCK = {".scr", ".pgm", ".java", ".js", ".sql", ".xml"}  # /* */ 를 쓰는 확장자


def strip_text(text: str, ext: str) -> str:
    markers = LINE_MARKERS.get(ext, [])
    block_ok = ext in BLOCK
    out: list[str] = []
    in_block = False
    for raw in text.splitlines():
        if ext in HASH_LINE and raw.lstrip().startswith("#"):
            continue
        buf, quote, i = [], None, 0
        while i < len(raw):
            ch = raw[i]
            if in_block:
                if raw.startswith("*/", i):
                    in_block, i = False, i + 2
                else:
                    i += 1
                continue
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
                buf.append(ch)
                i += 1
                continue
            if block_ok and raw.startswith("/*", i):
                in_block, i = True, i + 2
                continue
            if any(raw.startswith(m, i) for m in markers):
                break
            buf.append(ch)
            i += 1
        line = "".join(buf).rstrip()
        if line or not raw.strip():  # 주석만 있던 줄은 버리고, 원래 빈 줄은 남김
            out.append(line)
    collapsed: list[str] = []
    for line in out:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed) + "\n"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if dst.exists() and any(dst.iterdir()):
        print(f"출력 폴더가 비어 있지 않습니다: {dst}")
        return 1
    stripped = copied = 0
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        ext = path.suffix.lower()
        if ext in LINE_MARKERS or ext in BLOCK:
            target.write_text(strip_text(path.read_text(encoding="utf-8"), ext), encoding="utf-8")
            stripped += 1
        else:
            shutil.copy2(path, target)
            copied += 1
    print(f"주석 제거 {stripped}개, 그대로 복사 {copied}개 → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
