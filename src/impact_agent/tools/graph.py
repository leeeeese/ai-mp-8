"""영향도 그래프 조회 tool (Explorer 사용).

GraphStore 는 노드 검색·이웃 조회만 제공하고, 목표 역할 기준 탐색(traverse_graph)은 여기서 한다.
- Neo4jGraphStore: 운영 기본. full-text 인덱스(node_search)로 후보를 찾는다.
- CsvGraphStore: data/graph/*.csv 를 메모리에 올린다 (Neo4j 없이 개발·테스트).
두 백엔드 모두 후보 점수는 같은 함수(term_score)로 다시 매겨 결과가 같은 기준이 되게 한다.
"""

import csv
import json
import os
import re
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from ..rules import ROLE_RANK
from ..schemas import (
    Domain,
    GetNodeDetailInput,
    GraphEdge,
    GraphNode,
    MatchSource,
    NodeRole,
    SearchGraphNodesInput,
    SearchGraphNodesItem,
    StopReason,
    TraverseGraphInput,
    TraverseGraphOutput,
    TraversePath,
)

ROOT = Path(__file__).resolve().parents[3]
MAX_PATHS = 300  # traverse_graph 한 번에 기록할 경로 수 상한 (안전장치)
FORWARD_EDGES = {"CALLS", "OPENS", "INVOKES", "EXECUTES", "READS", "WRITES", "HAS_COLUMN"}
REVERSE_EDGES = FORWARD_EDGES - {"OPENS"}


# --- 검색 점수 ------------------------------------------------------------------


def _bigrams(text: str) -> set[str]:
    s = re.sub(r"[\s_().,:/\-]+", "", text.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def term_score(term: str, text: str) -> float:
    """term 이 text 에 얼마나 들어 있는지 (0~1). 통째로 포함되면 1, 아니면 2글자 단위 일치 비율."""
    if not term.strip():
        return 0.0
    if re.sub(r"\s+", "", term.lower()) in re.sub(r"\s+", "", text.lower()):
        return 1.0
    tb = _bigrams(term)
    return len(tb & _bigrams(text)) / len(tb)


def _term_weight(term: str, index: int) -> float:
    """요건 원문 표현(첫 번째)은 1, 동의어는 짧을수록 흔한 말이라 낮춘다 (2글자 0.8, 3글자 0.9, 4글자 이상 1)."""
    if index == 0:
        return 1.0
    n = len(re.sub(r"\s+", "", term))
    return 0.8 if n <= 2 else 0.9 if n == 3 else 1.0


def _score_node(node: GraphNode, terms: list[str]) -> tuple[float, str, MatchSource]:
    """가장 잘 맞는 표현의 (점수 × 가중치) + 함께 통째로 맞은 다른 표현마다 0.05."""
    text = node.properties.get("search_text") or node.name
    glossary = [t.lower() for t in node.properties.get("terms") or []]
    best = (0.0, terms[0], MatchSource.TEXT)
    full = 0
    for i, t in enumerate(terms):
        raw = term_score(t, text)
        src = MatchSource.GLOSSARY if any(term_score(t, g) == 1.0 for g in glossary) else MatchSource.TEXT
        if src is MatchSource.GLOSSARY:
            raw = 1.0
        full += raw == 1.0
        s = raw * _term_weight(t, i)
        if s > best[0]:
            best = (s, t, src)
    bonus = 0.05 * max(full - 1, 0)
    return (round(best[0] + bonus, 3) if best[0] else 0.0), best[1], best[2]


# --- 저장소 -------------------------------------------------------------------


class GraphStore(Protocol):
    def candidates(self, terms: list[str], domains: list[Domain], labels: list[str] | None, limit: int) -> list[GraphNode]: ...

    def node(self, node_id: str) -> GraphNode | None: ...

    def out_edges(self, node_id: str) -> list[GraphEdge]: ...

    def in_edges(self, node_id: str) -> list[GraphEdge]: ...


def _make_node(node_id, label, role, name, domain, source_file, source_line, props) -> GraphNode:
    return GraphNode(node_id=node_id, name=name, label=label, role=NodeRole(role) if role else None,
                     domain=Domain(domain) if domain else None, source_file=source_file or None,
                     source_line=int(source_line) if source_line not in (None, "") else None, properties=props)


def _edge_props(raw: dict) -> dict:
    props = dict(raw)
    if isinstance(props.get("columns"), str):
        props["columns"] = [c for c in props["columns"].split(",") if c]
    return props


class CsvGraphStore:
    def __init__(self, graph_dir: Path | None = None) -> None:
        graph_dir = graph_dir or ROOT / "data" / "graph"
        self.nodes: dict[str, GraphNode] = {}
        with open(graph_dir / "nodes.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.nodes[r["node_id"]] = _make_node(r["node_id"], r["label"], r.get("role"), r["name"], r["domain"],
                                                      r["source_file"], r["source_line"], json.loads(r["properties"]))
        self._out: dict[str, list[GraphEdge]] = {}
        self._in: dict[str, list[GraphEdge]] = {}
        with open(graph_dir / "edges.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                e = GraphEdge(from_id=r["from_id"], to_id=r["to_id"], type=r["type"],
                              properties=_edge_props(json.loads(r["properties"])))
                self._out.setdefault(e.from_id, []).append(e)
                self._in.setdefault(e.to_id, []).append(e)

    def candidates(self, terms, domains, labels, limit):
        pool = [n for n in self.nodes.values()
                if (not domains or n.domain in domains) and (not labels or n.label in labels)]
        scored = sorted(pool, key=lambda n: -_score_node(n, terms)[0])
        return scored[:limit]

    def node(self, node_id):
        return self.nodes.get(node_id)

    def out_edges(self, node_id):
        return self._out.get(node_id, [])

    def in_edges(self, node_id):
        return self._in.get(node_id, [])


LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:/\\]')


def _lucene(term: str) -> str:
    return LUCENE_SPECIAL.sub(" ", term).strip()


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self._nodes: dict[str, GraphNode | None] = {}
        self._edges: dict[tuple[str, str], list[GraphEdge]] = {}

    def _run(self, query: str, **params) -> list[dict]:
        with self.driver.session() as s:
            return s.run(query, **params).data()

    @staticmethod
    def _to_node(n: dict, label: str) -> GraphNode:
        props = {k: v for k, v in n.items()
                 if k not in ("node_id", "name", "role", "domain", "source_file", "source_line")}
        return _make_node(n["node_id"], label, n.get("role"), n["name"], n.get("domain"), n.get("source_file"),
                          n.get("source_line"), props)

    def candidates(self, terms, domains, labels, limit):
        # Lucene 특수문자를 지우고 표현마다 OR. cjk 분석기가 한글을 2글자 단위로 나눠 부분 일치를 찾는다
        query = " OR ".join(f"({_lucene(t)})" for t in terms if _lucene(t))
        rows = self._run(
            "CALL db.index.fulltext.queryNodes('node_search', $q, {limit: 500}) YIELD node, score "
            "WITH node, score, coalesce([l IN labels(node) WHERE NOT l IN ['Node', node.role]][0], node.role) AS label "
            "WHERE (size($domains) = 0 OR node.domain IN $domains) AND ($labels IS NULL OR label IN $labels) "
            "RETURN properties(node) AS n, label ORDER BY score DESC LIMIT $limit",
            q=query, domains=[d.value for d in domains], labels=labels, limit=limit)
        nodes = [self._to_node(r["n"], r["label"]) for r in rows]
        for n in nodes:
            self._nodes[n.node_id] = n
        return nodes

    def node(self, node_id):
        if node_id not in self._nodes:
            rows = self._run("MATCH (n:Node {node_id: $id}) "
                             "RETURN properties(n) AS n, coalesce([l IN labels(n) WHERE NOT l IN ['Node', n.role]][0], n.role) AS label",
                             id=node_id)
            self._nodes[node_id] = self._to_node(rows[0]["n"], rows[0]["label"]) if rows else None
        return self._nodes[node_id]

    def _edges_of(self, node_id: str, direction: str) -> list[GraphEdge]:
        key = (node_id, direction)
        if key not in self._edges:
            pattern = "(a:Node {node_id: $id})-[r]->(b:Node)" if direction == "out" else "(b:Node)-[r]->(a:Node {node_id: $id})"
            rows = self._run(f"MATCH {pattern} RETURN a.node_id AS a, b.node_id AS b, type(r) AS t, properties(r) AS p",
                             id=node_id)
            self._edges[key] = [
                GraphEdge(from_id=r["a"] if direction == "out" else r["b"], to_id=r["b"] if direction == "out" else r["a"],
                          type=r["t"], properties=_edge_props(r["p"]))
                for r in rows
            ]
        return self._edges[key]

    def out_edges(self, node_id):
        return self._edges_of(node_id, "out")

    def in_edges(self, node_id):
        return self._edges_of(node_id, "in")


@lru_cache(maxsize=1)
def get_graph_store() -> GraphStore:
    """GRAPH_BACKEND=csv 이면 data/graph CSV, 아니면 .env 의 Neo4j."""
    if os.environ.get("GRAPH_BACKEND", "neo4j").lower() == "csv":
        return CsvGraphStore(Path(os.environ["GRAPH_DIR"]) if os.environ.get("GRAPH_DIR") else None)
    return Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])


# --- tool -----------------------------------------------------------------------


def search_graph_nodes(params: SearchGraphNodesInput, store: GraphStore) -> list[SearchGraphNodesItem]:
    """표현(phrase·synonyms)으로 시작 후보 노드를 찾는다. 점수가 0 인 후보는 버린다."""
    pool = store.candidates(params.terms, params.domains, params.labels, params.top_k * 4)
    items = []
    for n in pool:
        score, term, src = _score_node(n, params.terms)
        if score > 0:
            items.append(SearchGraphNodesItem(node_id=n.node_id, name=n.name, label=n.label, role=n.role,
                                              domain=n.domain, score=round(score, 3), matched_term=term, match_source=src))
    items.sort(key=lambda i: (-i.score, i.node_id))
    return items[:params.top_k]


def get_node_detail(params: GetNodeDetailInput, store: GraphStore) -> GraphNode | None:
    return store.node(params.node_id)


def traverse_graph(params: TraverseGraphInput, store: GraphStore) -> TraverseGraphOutput:
    """시작 노드마다 목표 역할에 닿거나 hop 상한에 걸릴 때까지 넓이 우선으로 탐색한다.

    - 목표 역할에 닿으면 멈춤 (target_reached)
    - 목표 역할을 건너뛰면 (예: tos Batch → SQL, 목표 Logic) 건너뛴 노드에서 멈춤 (nearest_role)
    - Screen → Screen(OPENS)은 시작 화면에서 한 단계만 따라간다 (화면 이동 연쇄 방지)
    - hop 은 역할이 바뀌는 단계만 센다. 같은 역할 사이 호출(Service→Service 자기 호출, Screen→Screen)은 세지 않고,
      순환은 경로 안 방문 노드로 막음
    - 같은 노드라도 경로가 다르면 따로 기록한다 (경로 수가 MAX_PATHS 를 넘으면 더 펼치지 않음)
    - exclude_common 이면 공통 노드는 확장하지 않고 excluded_common 에 남김 (시작 노드는 예외)
    - Column 은 목표가 Column 일 때만 확장: 그 테이블에 focus_ids 컬럼이 있으면 그 컬럼만,
      없으면 들어온 접근 엣지의 columns, 시작 노드가 테이블이면 컬럼 전체
    - 역방향으로 Column → Table 을 지나면, Table → SQL/DBIO 는 그 컬럼을 쓰는 엣지만 따라감
      (컬럼 없이 Table 에서 출발하고 writes_only 면 WRITES 엣지만 따라감)
    """
    forward = params.direction == "forward"
    target = ROLE_RANK[params.target_role]
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple, GraphEdge] = {}
    paths: dict[tuple, TraversePath] = {}
    excluded: set[str] = set()
    frontier: set[str] = set()

    def record(path: list[str], role: NodeRole | None, reason: StopReason) -> None:
        ordered = path if forward else list(reversed(path))
        paths.setdefault(tuple(ordered), TraversePath(nodes=ordered, reached_role=role, stop_reason=reason))

    for start in params.start_ids:
        if store.node(start) is None:
            continue
        queue = deque([(start, [start], None, None, 0)])  # (노드, 경로, 들어온 엣지, 경로상 컬럼, hop)
        while queue and len(paths) < MAX_PATHS:
            nid, path, via, column, hops = queue.popleft()
            node = store.node(nid)
            nodes[nid] = node
            rank = ROLE_RANK[node.role]
            first = len(path) == 1
            if rank == target:
                record(path, node.role, StopReason.TARGET_REACHED)
                continue
            if not first and (rank > target if forward else rank < target):
                record(path, node.role, StopReason.NEAREST_ROLE)
                continue
            if not first and params.exclude_common and node.properties.get("is_common"):
                excluded.add(nid)
                record(path, node.role, StopReason.EXCLUDED_COMMON)
                continue
            if hops >= params.hop_cap:
                frontier.add(nid)
                record(path, node.role, StopReason.HOP_CAP)
                continue

            nexts = []
            for e in store.out_edges(nid) if forward else store.in_edges(nid):
                if e.type not in (FORWARD_EDGES if forward else REVERSE_EDGES):
                    continue
                if e.type == "OPENS" and not first:  # 화면 이동은 시작 화면이 여는 화면(팝업 등)까지만
                    continue
                nxt = e.to_id if forward else e.from_id
                if forward and e.type == "HAS_COLUMN":
                    if params.target_role is not NodeRole.COLUMN:
                        continue
                    table = nid.split(":", 1)[1]
                    focus = {f.split(".", 1)[1] for f in params.focus_ids if f.startswith(f"COL:{table}.")}
                    allowed = focus or set(via.properties.get("columns") or []) if via else focus
                    if allowed and nxt.split(".", 1)[1] not in allowed:
                        continue
                if not forward and column and e.type in ("READS", "WRITES") and column not in (e.properties.get("columns") or []):
                    continue
                if not forward and not column and params.writes_only and e.type == "READS":
                    continue
                if nxt in path:
                    continue
                nxt_node = store.node(nxt)
                if nxt_node is not None:
                    nexts.append((nxt_node, e))
            if not nexts:
                record(path, node.role, StopReason.DEAD_END)
                continue
            for nxt_node, e in nexts:
                edges[(e.from_id, e.to_id, e.type)] = e
                col = nid.split(".", 1)[1] if not forward and node.role is NodeRole.COLUMN else column
                step = 0 if nxt_node.role is node.role else 1
                queue.append((nxt_node.node_id, path + [nxt_node.node_id], e, col, hops + step))

    for n in excluded:
        nodes.setdefault(n, store.node(n))
    return TraverseGraphOutput(
        nodes=list(nodes.values()), edges=list(edges.values()), paths=list(paths.values()),
        excluded_common=sorted(excluded), frontier=sorted(frontier),
        hops_used=max((_role_hops(p.nodes, store) for p in paths.values()), default=0),
    )


def _role_hops(path: list[str], store: GraphStore) -> int:
    roles = [store.node(n).role for n in path]
    return sum(a is not b for a, b in zip(roles, roles[1:]))
