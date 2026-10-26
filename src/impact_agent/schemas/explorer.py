"""Explorer 출력 및 그래프 공통 모델."""

from typing import Any

from pydantic import BaseModel, Field

from .enums import Direction, Domain, MatchSource, NodeRole, StopReason
from .react import ReactStep


class GraphNode(BaseModel):
    node_id: str
    name: str
    label: str = Field(description="영역별 노드 유형 (Screen/Controller/Service/SQL/TP/Module/Batch/DBIO/Table/Column)")
    role: NodeRole | None = Field(default=None, description="공통 역할 라벨")
    domain: Domain | None = None
    source_file: str | None = Field(default=None, description="영역 소스 루트 기준 상대경로")
    source_line: int | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    from_id: str
    to_id: str
    type: str = Field(description="CALLS / OPENS / INVOKES / EXECUTES / READS / WRITES / REFERENCES / HAS_COLUMN")
    properties: dict[str, Any] = Field(default_factory=dict, description="event, note, via, columns")


class StartNodeChoice(BaseModel):
    item_id: int | None = None
    node_id: str
    matched_keyword: str = Field(description="일치한 표현 (phrase 또는 synonym)")
    match_source: MatchSource = MatchSource.TEXT
    score: float | None = None
    reason: str


class ExplorePath(BaseModel):
    """탐색 경로 1개. nodes 는 탐색 방향과 관계없이 진입 → DB (호출되는 쪽) 순서."""

    item_id: int
    nodes: list[str] = Field(min_length=1)
    direction: Direction = Field(description="이 경로를 찾은 탐색 방향 (forward / reverse)")
    reached_role: NodeRole | None = Field(default=None, description="탐색이 멈춘 노드의 역할")
    stop_reason: StopReason


class ItemExploreResult(BaseModel):
    item_id: int
    start_node_ids: list[str] = Field(default_factory=list)
    target_role: NodeRole
    direction: Direction
    reached: bool = Field(description="목표 역할에 닿은 경로가 하나 이상 있는지")
    hops_used: int = 0
    frontier: list[str] = Field(default_factory=list, description="hop 상한에 걸려 더 확장하지 못한 노드")
    unmatched_mentions: list[str] = Field(default_factory=list, description="노드를 찾지 못한 언급 표현")


class ExplorerOutput(BaseModel):
    start_nodes: list[StartNodeChoice]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    paths: list[ExplorePath] = Field(default_factory=list)
    item_results: list[ItemExploreResult] = Field(default_factory=list)
    excluded_common: list[str] = Field(default_factory=list, description="공통이라 확장하지 않은 노드")
    unresolved: list[str] = Field(default_factory=list, description="그래프 밖 영역·신규 후보·매칭 실패 (보고서에서 분석 완료처럼 쓰지 않음)")
    hops_used: int = Field(default=0, ge=0, description="경로 중 가장 긴 hop 수")
    tool_call_count: int = 0
    react_trace: list[ReactStep] = Field(default_factory=list, description="규칙 탐색 뒤 ReAct 보완 탐색 기록")
