"""Planner → Explorer 연계 기준표.

- CHANGE_TYPE_RULES: 요건 유형 → 목표 역할·방향·hop 상한 (Planner 는 유형만 고르고 나머지는 이 표로 정한다)
- SM_PART_DOMAINS: SM 선택 → 탐색 영역
- ROLE_ORDER: 진입 → DB 순서의 역할 단계 (Explorer 멈춤 판정)
- MENTION_LABELS: 언급 종류 → 시작 노드로 찾을 영역별 라벨
- path_areas / build_impact_paths: 역할 → 영역(screen·db·interface·batch) 분류, Explorer 경로 → 영향 경로 골격
  (Simulator · Verifier · Composer 공통)
- shared_nodes: 공유 노드(진입점 2개 이상·공통 모듈). Simulator 난이도 보정과 Composer 상세 분석 권장이 함께 쓴다
"""

from . import config
from .schemas import (
    Area,
    ChangeType,
    Confidence,
    Direction,
    Domain,
    Evidence,
    ExplorePath,
    GraphEdge,
    GraphNode,
    ImpactPath,
    MentionKind,
    NodeRole,
    PathHop,
    SmPart,
)

F, R, FR = Direction.FORWARD, Direction.REVERSE, Direction.FORWARD_THEN_REVERSE

CHANGE_TYPE_RULES: dict[ChangeType, tuple[NodeRole, Direction]] = {
    ChangeType.SCREEN_CHANGE: (NodeRole.ENTRY_HANDLER, F),  # 화면 입력·표시 변경
    ChangeType.LOGIC_CHANGE: (NodeRole.LOGIC, F),  # 검증·처리 로직 변경
    ChangeType.QUERY_FIELD_ADD: (NodeRole.DATA_ACCESS, F),  # 조회 결과 항목 추가
    ChangeType.DATA_ADD: (NodeRole.TABLE, FR),  # 저장 데이터 추가
    ChangeType.COLUMN_SPEC_CHANGE: (NodeRole.ENTRY, R),  # 컬럼 길이·타입 변경 (컬럼에서 출발)
    ChangeType.BATCH_CHANGE: (NodeRole.DATA_ACCESS, F),  # 배치 처리 변경
    ChangeType.INTERFACE_FIELD_ADD: (NodeRole.ENTRY, R),  # 연계 항목 추가 (컬럼에서 배치까지, 그래프 밖 영역 표시)
    ChangeType.OPS_CAUSE: (NodeRole.TABLE, F),  # 운영 이슈 원인 확인 (진입부터 데이터까지)
    ChangeType.STRUCTURE_QUERY: (NodeRole.COLUMN, F),  # 구조·데이터 위치 질의 (테이블·컬럼 확인)
}


def hop_cap(direction: Direction) -> int:
    return config.MAX_HOPS_BIDIRECTIONAL if direction is FR else config.MAX_HOPS_FORWARD


SM_PART_DOMAINS: dict[SmPart, list[Domain]] = {
    SmPart.MARKETING: [Domain.TOS],
    SmPart.PRODUCT: [Domain.SWING],
}

# tool 호출 한도(config.MAX_TOOL_CALLS) 배분. Supervisor 는 재탐색 라우팅에, Explorer·Retriever 는 ReAct 예산에 쓴다
RESERVED_FOR_RETRIEVER = 5  # Explorer ReAct 가 남겨 둘 몫 (Retriever 원문 읽기 1 + ReAct 보완 4)
MIN_CALLS_FOR_REEXPLORE = 2  # 재탐색 라운드를 시작할 최소 남은 호출 (Explorer 탐색 1 + Retriever 새 노드 읽기 1)
EXPLORER_MAX_REACT_STEPS = 8  # Explorer ReAct 단계 상한 (finish 포함)
RETRIEVER_MAX_REACT_STEPS = 6

ROLE_ORDER = [NodeRole.ENTRY, NodeRole.ENTRY_HANDLER, NodeRole.LOGIC, NodeRole.DATA_ACCESS, NodeRole.TABLE, NodeRole.COLUMN]
ROLE_RANK = {role: i for i, role in enumerate(ROLE_ORDER)}

MENTION_LABELS: dict[MentionKind, list[str] | None] = {
    MentionKind.SCREEN: ["Screen"],
    MentionKind.BATCH: ["Batch"],
    MentionKind.FUNCTION: ["Controller", "Service", "SQL", "TP", "Module", "DBIO"],
    MentionKind.TABLE: ["Table"],
    MentionKind.COLUMN: ["Column"],
    MentionKind.INTERFACE: ["Batch", "TP", "Module"],
    MentionKind.TERM: None,
}


# --- Simulator · Verifier · Composer 공통 (docs/SE_agent_design.md 0장 영역 분류, 1장 경로 골격) -------------

# 역할 라벨이 비어 있는 노드용 (영역별 라벨 → 역할)
LABEL_ROLES: dict[str, NodeRole] = {
    "Screen": NodeRole.ENTRY, "Batch": NodeRole.ENTRY, "Controller": NodeRole.ENTRY_HANDLER, "TP": NodeRole.ENTRY_HANDLER,
    "Service": NodeRole.LOGIC, "Module": NodeRole.LOGIC, "SQL": NodeRole.DATA_ACCESS, "DBIO": NodeRole.DATA_ACCESS,
    "Table": NodeRole.TABLE, "Column": NodeRole.COLUMN,
}
ENTRY_AREAS: dict[str, Area] = {"Screen": Area.SCREEN, "Batch": Area.BATCH}
DB_ROLES = {NodeRole.DATA_ACCESS, NodeRole.TABLE, NodeRole.COLUMN}
AREA_ORDER = [Area.SCREEN, Area.DB, Area.INTERFACE, Area.BATCH]
FIRST_RELATION = "ENTRY"  # 경로 첫 hop 의 relation


def node_role(node: GraphNode | None) -> NodeRole | None:
    if node is None:
        return None
    return node.role or LABEL_ROLES.get(node.label)


def interface_node_ids(nodes: dict[str, GraphNode], evidence: list[Evidence]) -> set[str]:
    """인터페이스가 걸린 노드: 노드 속성 interfaces 가 있거나 인터페이스 정의서 근거가 있는 노드."""
    ids = {nid for nid, n in nodes.items() if n.properties.get("interfaces")}
    return ids | {e.node_id for e in evidence if e.node_id and e.source_kind == "interface_spec"}


def node_areas(path: list[str], nodes: dict[str, GraphNode], interface_ids: set[str]) -> list[set[Area]]:
    """경로의 노드별 영역 (path 와 같은 순서).

    Entry 는 라벨로 screen / batch, DataAccess·Table·Column 은 db, EntryHandler·Logic 은 경로 진입 노드의 영역,
    interface 는 인터페이스가 걸린 노드에 추가. 진입 노드 없이 EntryHandler 에서 시작한 경로는 screen 으로 본다.
    """
    roles = [node_role(nodes.get(n)) for n in path]
    entry = next((ENTRY_AREAS.get(nodes[n].label) for n, r in zip(path, roles) if r is NodeRole.ENTRY and n in nodes), None)
    if entry is None and NodeRole.ENTRY_HANDLER in roles:
        entry = Area.SCREEN
    out = []
    for n, r in zip(path, roles):
        areas = set()
        if r is NodeRole.ENTRY:
            areas.add(ENTRY_AREAS.get(nodes[n].label, entry or Area.SCREEN))
        elif r in DB_ROLES:
            areas.add(Area.DB)
        elif r in (NodeRole.ENTRY_HANDLER, NodeRole.LOGIC) and entry is not None:
            areas.add(entry)
        if n in interface_ids:
            areas.add(Area.INTERFACE)
        out.append(areas)
    return out


def path_areas(path: list[str], nodes: dict[str, GraphNode], interface_ids: set[str]) -> list[Area]:
    """경로가 걸치는 영역 (AREA_ORDER 순) = 노드별 영역의 합."""
    areas = set().union(*node_areas(path, nodes, interface_ids))
    return [a for a in AREA_ORDER if a in areas]


def shared_nodes(paths: list[ExplorePath], nodes: dict[str, GraphNode]) -> dict[str, str]:
    """공유 노드 → 사유 (설계서 1-3 (c), 1-4).

    Simulator `[공유 노드]`·난이도 보정과 Composer 상세 분석 권장 신호가 같은 정의를 쓴다. 진입점 = Explorer 경로 첫 노드가 Entry 인 것.

    서로 다른 진입점 2개 이상의 경로가 지나는 노드, 또는 is_common 노드. 진입점 수는 Explorer 가 찾은 경로 안에서 센다
    (그래프 전체보다 적을 수 있음 → 난이도를 과하게 올리지 않는 쪽).
    """
    entries: dict[str, set[str]] = {}
    for p in paths:
        if p.nodes and node_role(nodes.get(p.nodes[0])) is NodeRole.ENTRY:
            for n in p.nodes[1:]:
                entries.setdefault(n, set()).add(p.nodes[0])
    out = {n: f"진입점 {len(es)}개" for n, es in entries.items() if len(es) >= 2}
    for nid, n in nodes.items():
        if n.properties.get("is_common"):
            out[nid] = f"{out[nid]}, 공통 모듈" if nid in out else "공통 모듈"
    return out


def evidence_by_node(evidence: list[Evidence]) -> dict[str, Evidence]:
    """노드별 첫 근거."""
    out: dict[str, Evidence] = {}
    for e in evidence:
        if e.node_id:
            out.setdefault(e.node_id, e)
    return out


def evidence_ref(e: Evidence) -> str:
    return f"{e.file_path}#{e.location}" if e.location else e.file_path


def build_impact_paths(
    paths: list[ExplorePath], edges: list[GraphEdge], evidence: list[Evidence], impact: str = "영향 검토 필요"
) -> list[tuple[ImpactPath, list[ExplorePath]]]:
    """Explorer 경로 → ImpactPath 골격 (같은 노드 순서는 합침). 경로마다 원래 Explorer 경로 목록을 같이 돌려준다.

    hop: relation = 앞 노드 → 이 노드 엣지 유형 (첫 hop 은 ENTRY), evidence_ref = 노드 근거 위치,
    confidence = 근거가 있으면 confirmed, 없으면 inferred. path_id = P<item_id>-<n>.
    """
    relation = {(e.from_id, e.to_id): e.type for e in edges}
    ev = evidence_by_node(evidence)
    merged: dict[tuple[str, ...], tuple[ImpactPath, list[ExplorePath]]] = {}
    count: dict[int, int] = {}
    for p in paths:
        key = tuple(p.nodes)
        if key in merged:
            merged[key][1].append(p)
            continue
        count[p.item_id] = count.get(p.item_id, 0) + 1
        hops = [
            PathHop(
                node=n,
                relation=FIRST_RELATION if i == 0 else relation.get((p.nodes[i - 1], n), "-"),
                impact=impact,
                evidence_ref=evidence_ref(ev[n]) if n in ev else None,
                confidence=Confidence.CONFIRMED if n in ev else Confidence.INFERRED,
            )
            for i, n in enumerate(p.nodes)
        ]
        merged[key] = (ImpactPath(path_id=f"P{p.item_id}-{count[p.item_id]}", hops=hops), [p])
    return list(merged.values())
