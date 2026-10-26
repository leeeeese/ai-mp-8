"""소스·문서 직접 조회 tool (Retriever 사용). 벡터 검색 없이 그래프 노드가 가리키는 원문을 읽는다.

- 소스: 영역 소스 루트(SOURCE_ROOT_<DOMAIN>) / node.source_file 의 source_line 부터 노드 범위
  (범위 = 그래프 code 속성 줄 수. Screen 의 js, Batch 의 .pc 처럼 code 에 붙은 파일은 파일 전체를 이어 붙임)
- 인터페이스 정의서: 노드 interfaces 속성의 ID → 문서 루트(DOCS_ROOT_<DOMAIN>)/interface 에서 찾음
"""

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..schemas import Domain, GraphNode, ReadSourceInput, ReadSourceItem
from .graph import GraphStore

ROOT = Path(__file__).resolve().parents[3]
ATTACH = re.compile(r"\n\n// ---- (.+?) ----\n")


@dataclass(frozen=True)
class SourceRoots:
    data: Path
    source: dict[Domain, Path]
    docs: dict[Domain, Path]

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.data.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


@lru_cache(maxsize=1)
def get_source_roots() -> SourceRoots:
    def env(key: str, default: str) -> Path:
        return (ROOT / os.environ.get(key, default)).resolve()

    return SourceRoots(
        data=env("DATA_ROOT", "data"),
        source={d: env(f"SOURCE_ROOT_{d.value.upper()}", f"data/source/{d.value}") for d in Domain},
        docs={d: env(f"DOCS_ROOT_{d.value.upper()}", f"data/docs/{d.value}") for d in Domain},
    )


def _read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _source_item(node: GraphNode, roots: SourceRoots, context: int) -> ReadSourceItem:
    root = roots.source.get(node.domain) if node.domain else None
    if root is None or not node.source_file:
        return ReadSourceItem(node_id=node.node_id, source_kind="source", file_path="-", content="", found=False)
    path = root / node.source_file
    lines = _read_lines(path)
    if lines is None:
        return ReadSourceItem(node_id=node.node_id, source_kind="source", file_path=roots.rel(path), content="", found=False)

    code = node.properties.get("code") or ""
    main, *attached = ATTACH.split(code) if code else [""]
    start = node.source_line or 1
    end = start + max(len(main.splitlines()), 1) - 1 if main else start
    lo, hi = max(start - context, 1), min(end + context, len(lines))
    parts = ["\n".join(lines[lo - 1:hi])]
    if node.label == "Column" and node.properties.get("comment"):
        parts.append(f"-- 코멘트: {node.properties['comment']}")
    for rel_path in attached[0::2]:  # split 결과: [본문, 파일명, 내용, 파일명, 내용 ...]
        extra = _read_lines(root / rel_path)
        if extra is not None:
            parts.append(f"// ---- {rel_path} ----\n" + "\n".join(extra))
    kind = "screen" if node.label == "Screen" else "source"
    return ReadSourceItem(node_id=node.node_id, source_kind=kind, file_path=roots.rel(path), start_line=lo, end_line=hi,
                          content="\n\n".join(parts))


def _interface_items(node: GraphNode, roots: SourceRoots) -> list[ReadSourceItem]:
    doc_root = roots.docs.get(node.domain) if node.domain else None
    if doc_root is None:
        return []
    items = []
    for entry in node.properties.get("interfaces") or []:
        if_id = str(entry).split()[0]
        found = None
        for path in sorted((doc_root / "interface").glob("*")) if (doc_root / "interface").is_dir() else []:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if path.stem == if_id:
                found = (path, text)
                break
            block = re.search(rf"^#\s*\[IF:{re.escape(if_id)}\].*?(?=^#\s*\[IF:|\Z)", text, re.M | re.S)
            if block:
                found = (path, block.group(0).strip())
                break
        if found:
            items.append(ReadSourceItem(node_id=node.node_id, source_kind="interface_spec", file_path=roots.rel(found[0]),
                                        content=found[1]))
    return items


def read_source(params: ReadSourceInput, store: GraphStore, roots: SourceRoots | None = None) -> list[ReadSourceItem]:
    """노드마다 소스 원문을 읽고, 인터페이스가 걸린 노드는 정의서도 함께 돌려준다."""
    roots = roots or get_source_roots()
    items = []
    for nid in params.node_ids:
        node = store.node(nid)
        if node is None:
            items.append(ReadSourceItem(node_id=nid, source_kind="source", file_path="-", content="", found=False))
            continue
        items.append(_source_item(node, roots, params.context_lines))
        items += _interface_items(node, roots)
    return items
