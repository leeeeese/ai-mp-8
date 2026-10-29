"""적재 현황 API (UI-04 `/graph`): Neo4j 그래프를 읽어 frontend `GraphStatus` · `Neighborhood` 모양으로 돌려준다.

- 그래프에 쓰지 않는다. 재적재 요청은 요청만 JSON 파일에 쌓는다 (승인·실행은 관리자 → 적재 파이프라인).
- master 대비: data/ 가 git 밖이라 커밋 대신 소스 지문으로 비교한다. `build_graph.py --load` 가 영역별 :LoadMeta
  (파일별 sha1)를 남기고, 여기서 SOURCE_ROOT_<DOMAIN> 의 현재 파일과 비교한다.
  loadedCommit / masterCommit = 적재 시점 / 현재 지문 앞 7자, behindCommits = 지문이 다르면 1, 같으면 0.
- 응답 필드는 목업(frontend/src/mocks/handlers.ts)과 같다. 노드 속성은 scripts/export_ui_graph_sample.py 와 같은 것만 넘긴다.
- GraphSource: Neo4jGraphSource(운영) · CsvGraphSource(data/graph CSV, 테스트).
"""

import csv
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .runner import KpiStore

ROOT = Path(__file__).resolve().parents[3]
SM_TO_DOMAIN = {"dbm": "tos", "prod": "swing"}
ROLE = {"Screen": "Entry", "Batch": "Entry", "Controller": "EntryHandler", "TP": "EntryHandler", "Service": "Logic",
        "Module": "Logic", "SQL": "DataAccess", "DBIO": "DataAccess", "Table": "Table", "Column": "Column"}
KEEP_PROPS = ("desc", "comment", "program", "url", "stmt_type", "is_common", "table", "interfaces")
KEEP_EDGE = ("columns", "via", "event", "note")
SEARCH_LIMIT = 12
CACHE_SEC = 30  # 같은 영역 그래프를 다시 읽기 전까지 (검색창 타이핑마다 Neo4j 를 전부 읽지 않게)
MISSING_PROGRAM = re.compile(r"\(PROC (\w+)\)")


@dataclass
class Graph:
    nodes: list[dict]  # {node_id, label, role, name, domain, source_file, source_line, properties}
    edges: list[dict]  # {from_id, to_id, type, properties}
    by_id: dict[str, dict] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {n["node_id"]: n for n in self.nodes}


class GraphSource(Protocol):
    def load(self, domain: str) -> Graph: ...

    def load_metas(self, domain: str) -> list[dict]: ...  # 최근 순


class Neo4jGraphSource:
    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def _run(self, query: str, **params) -> list[dict]:
        with self.driver.session() as s:
            return s.run(query, **params).data()

    def load(self, domain):
        rows = self._run("MATCH (n:Node {domain: $d}) "
                         "RETURN n {.*, code: null} AS n, coalesce([l IN labels(n) WHERE NOT l IN ['Node', n.role]][0], n.role) AS label",
                         d=domain)
        nodes = []
        for r in rows:
            p = {k: v for k, v in r["n"].items() if v is not None}
            nodes.append({"node_id": p.pop("node_id"), "label": r["label"], "role": p.pop("role", None), "name": p.pop("name", ""),
                          "domain": p.pop("domain", domain), "source_file": p.pop("source_file", None),
                          "source_line": p.pop("source_line", None), "properties": p})
        edges = self._run("MATCH (a:Node {domain: $d})-[r]->(b:Node) "
                          "RETURN a.node_id AS from_id, b.node_id AS to_id, type(r) AS type, properties(r) AS properties", d=domain)
        return Graph(nodes, edges)

    def load_metas(self, domain):
        rows = self._run("MATCH (m:LoadMeta {domain: $d}) RETURN properties(m) AS m ORDER BY m.loaded_at DESC", d=domain)
        return [r["m"] for r in rows]


class CsvGraphSource:
    """data/graph/{nodes,edges}.csv. 적재 기록이 없어 load_metas 는 metas 로 넘긴 값 (테스트용)."""

    def __init__(self, graph_dir: Path, metas: list[dict] | None = None) -> None:
        self.graph_dir, self.metas = graph_dir, metas or []

    def load(self, domain):
        with open(self.graph_dir / "nodes.csv", encoding="utf-8") as f:
            nodes = [{**r, "properties": json.loads(r["properties"])} for r in csv.DictReader(f) if r["domain"] == domain]
        ids = {n["node_id"] for n in nodes}
        with open(self.graph_dir / "edges.csv", encoding="utf-8") as f:
            edges = [{**r, "properties": json.loads(r["properties"])} for r in csv.DictReader(f) if r["from_id"] in ids]
        return Graph(nodes, edges)

    def load_metas(self, domain):
        return sorted((m for m in self.metas if m["domain"] == domain), key=lambda m: m["loaded_at"], reverse=True)


# --- 소스 지문 (scripts/graph/model.py source_manifest 와 같은 규칙) -------------------------------


def source_manifest(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)}


def fingerprint(manifest: dict[str, str]) -> str:
    return hashlib.sha1("\n".join(f"{k} {v}" for k, v in manifest.items()).encode()).hexdigest()


def source_root(domain: str) -> Path:
    return (ROOT / os.environ.get(f"SOURCE_ROOT_{domain.upper()}", f"data/source/{domain}")).resolve()


# --- 응답 모양 ---------------------------------------------------------------------


def ui_node(n: dict) -> dict:
    p = n["properties"]
    return {"id": n["node_id"], "label": n["label"], "role": n["role"] or ROLE.get(n["label"], ""), "name": n["name"],
            "sourceFile": n["source_file"] or "", "sourceLine": int(n["source_line"] or 0),
            **{k: p[k] for k in KEEP_PROPS if p.get(k) not in (None, "", [])}}


def ui_edge(e: dict) -> dict:
    return {"from": e["from_id"], "to": e["to_id"], "type": e["type"],
            **{k: v for k, v in e["properties"].items() if k in KEEP_EDGE and v not in (None, "", [])}}


def master_sync(domain: str, metas: list[dict]) -> dict:
    now = source_manifest(source_root(domain))
    now_fp = fingerprint(now)
    if not metas:  # 적재 기록 없음 (:LoadMeta 를 남기기 전 적재) → 비교할 기준이 없다
        return {"percent": 0, "loadedCommit": "-", "masterCommit": now_fp[:7], "behindCommits": 1, "pendingFiles": [],
                "loadedAt": ""}
    last = metas[0]
    loaded = dict(zip(last["files"], last["hashes"]))
    files = sorted(set(loaded) | set(now))
    pending = [f for f in files if loaded.get(f) != now.get(f)]
    return {"percent": round(100 * (len(files) - len(pending)) / len(files)) if files else 100,
            "loadedCommit": last["fingerprint"][:7], "masterCommit": now_fp[:7], "behindCommits": int(last["fingerprint"] != now_fp),
            "pendingFiles": [f if f in now else f"{f} (삭제)" for f in pending], "loadedAt": last["loaded_at"]}


def quality_checks(domain: str, g: Graph) -> list[dict]:
    degree = Counter([e["from_id"] for e in g.edges] + [e["to_id"] for e in g.edges])
    missing = []
    for n in g.nodes:
        if n["label"] == "Batch":
            loaded = set((n["properties"].get("program") or "").split(","))
            missing += [f'{n["node_id"]} → {p}.pc' for s in n["properties"].get("steps") or []
                        for p in MISSING_PROGRAM.findall(s) if p not in loaded]
    checks = [
        ("isolated", "고립 노드", "어떤 엣지에도 연결되지 않은 노드", [n["node_id"] for n in g.nodes if degree[n["node_id"]] == 0]),
        ("rw-columns", "columns 누락 READS/WRITES", "SQL·DBIO → Table 엣지에 접근 컬럼이 비어 있음 (역방향 컬럼 필터가 동작하지 않음)",
         [f'{e["from_id"]} → {e["to_id"]}' for e in g.edges if e["type"] in ("READS", "WRITES") and not e["properties"].get("columns")]),
        ("source-file", "source_file 누락", "원문 위치가 없어 Retriever 가 근거를 읽을 수 없음",
         [n["node_id"] for n in g.nodes if not n["source_file"]]),
        ("meta", "정형 메타 미적재", "설명·코멘트가 없어 검색 문구(search_text)가 노드 ID 뿐인 노드",
         [n["node_id"] for n in g.nodes if n["label"] != "Column" and not n["properties"].get("desc") and not n["properties"].get("comment")]),
        ("missing-source", "미제공 소스 영역", "Job 정의는 있으나 프로그램 소스가 적재 범위 밖", missing),
    ]
    return [{"id": i, "title": t, "description": d, "items": items} for i, t, d, items in checks]


def graph_status(sm: str, g: Graph, metas: list[dict]) -> dict:
    domain = SM_TO_DOMAIN[sm]
    root = source_root(domain)
    labels = Counter(n["label"] for n in g.nodes)
    order = [lb for lb in ROLE if labels[lb]] + sorted(lb for lb in labels if lb not in ROLE)
    mapped = sum(bool(n["source_file"]) and (root / n["source_file"]).is_file() for n in g.nodes)
    return {
        "sm": sm, "connected": True, "masterSync": master_sync(domain, metas), "nodes": len(g.nodes), "rels": len(g.edges),
        "sourceMapRate": mapped / len(g.nodes) if g.nodes else 0,
        "labels": [{"label": lb, "role": ROLE.get(lb, ""), "count": labels[lb]} for lb in order],
        "checks": quality_checks(domain, g),
        "history": [{"id": f"h{len(metas) - i}", "at": m["loaded_at"], "commit": m["fingerprint"][:7], "nodes": m["nodes"],
                     "rels": m["rels"], "by": "적재 파이프라인", "result": "warning" if m.get("warnings") else "success"}
                    for i, m in enumerate(metas)],
    }


def search(g: Graph, q: str) -> list[dict]:
    """node_id·이름·설명 부분 일치. node_id(접두어 TBL: 등 포함·제외) 일치 > 앞부분 > 포함 > 이름 > 설명 순, 같으면 컬럼은 뒤."""
    q = q.strip().lower()

    def rank(n: dict) -> int | None:
        nid, name, desc = n["node_id"].lower(), n["name"].lower(), str(n["properties"].get("desc") or "").lower()
        local = nid.split(":", 1)[-1]
        for i, hit in enumerate((q in (nid, local), nid.startswith(q) or local.startswith(q), q in nid, q in name, q in desc)):
            if hit:
                return i
        return None

    hits = sorted(((r, n["label"] == "Column", n["node_id"]) for n in g.nodes if (r := rank(n)) is not None))
    return [{"id": g.by_id[i]["node_id"], "name": g.by_id[i]["name"], "label": g.by_id[i]["label"]} for _, _, i in hits[:SEARCH_LIMIT]]


def neighbors(g: Graph, center: str, hops: int) -> dict:
    """시작 노드에서 방향 무시 hops 단계까지. 노드마다 처음 닿은 hop."""
    hop = {center: 0}
    frontier = {center}
    for h in range(1, hops + 1):
        nxt = set()
        for e in g.edges:
            for a, b in ((e["from_id"], e["to_id"]), (e["to_id"], e["from_id"])):
                if a in frontier and b not in hop:
                    hop[b] = h
                    nxt.add(b)
        frontier = nxt
    return {"center": center,
            "nodes": [{**ui_node(g.by_id[i]), "hop": h} for i, h in hop.items() if i in g.by_id],
            "edges": [ui_edge(e) for e in g.edges if e["from_id"] in hop and e["to_id"] in hop]}


# --- 라우터 -----------------------------------------------------------------------


class ReloadRequestBody(BaseModel):
    sm: Literal["prod", "dbm"]
    scope: str
    reason: str


def default_source() -> GraphSource:
    if os.environ.get("GRAPH_BACKEND", "neo4j").lower() == "csv":
        return CsvGraphSource(Path(os.environ.get("GRAPH_DIR") or ROOT / "data" / "graph"))
    return Neo4jGraphSource(os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])


def graph_router(source_factory: Callable[[], GraphSource], reload_path: Path) -> APIRouter:
    """source_factory 는 처음 요청 때 한 번 부른다 (앱 생성만으로 Neo4j 에 붙지 않게)."""
    router = APIRouter(prefix="/api/graph")
    reload_requests = KpiStore(reload_path)  # 같은 JSON 목록 저장소 (최근 순)
    lock = threading.Lock()
    state: dict = {"source": None, "cache": {}}

    def source() -> GraphSource:
        with lock:
            if state["source"] is None:
                state["source"] = source_factory()
            return state["source"]

    def graph(sm: str) -> Graph:
        domain = SM_TO_DOMAIN[sm]
        hit = state["cache"].get(domain)
        if hit and time.monotonic() - hit[0] < CACHE_SEC:
            return hit[1]
        try:
            g = source().load(domain)
        except Exception as e:  # noqa: BLE001 - 연결 실패는 503 으로 알린다
            raise HTTPException(503, f"그래프 저장소에 연결할 수 없습니다: {e}") from e
        state["cache"][domain] = (time.monotonic(), g)
        return g

    @router.get("/status")
    def status(sm: Literal["prod", "dbm"] = "dbm") -> dict:
        try:
            g = graph(sm)
            metas = source().load_metas(SM_TO_DOMAIN[sm])
        except HTTPException:  # 연결이 끊겨도 화면은 그린다 (연결 끊김 배지)
            return {"sm": sm, "connected": False, "masterSync": master_sync(SM_TO_DOMAIN[sm], []), "nodes": 0, "rels": 0,
                    "sourceMapRate": 0, "labels": [], "checks": [], "history": []}
        return graph_status(sm, g, metas)

    @router.get("/search")
    def search_nodes(sm: Literal["prod", "dbm"] = "dbm", q: str = "") -> list[dict]:
        return search(graph(sm), q)

    @router.get("/neighbors")
    def node_neighbors(nodeId: str, sm: Literal["prod", "dbm"] = "dbm", hops: int = 2) -> dict:  # noqa: N803 - UI 쿼리 이름
        g = graph(sm)
        if nodeId not in g.by_id:
            raise HTTPException(404, "node not found")
        return neighbors(g, nodeId, max(1, min(hops, 3)))

    @router.post("/reload-requests")
    def create_reload_request(req: ReloadRequestBody) -> dict:
        r = {"id": "r" + uuid.uuid4().hex[:8], **req.model_dump(), "requestedBy": "현재 사용자",  # 인증 붙기 전 (목업과 같음)
             "requestedAt": datetime.now().astimezone().isoformat(timespec="seconds"), "status": "pending"}
        reload_requests.add(r)
        return r

    return router
