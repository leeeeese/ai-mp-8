"""Explorer 출력 및 그래프 공통 모델."""

from typing import Any

from pydantic import BaseModel, Field

from .enums import Domain, EdgeType, NodeLabel


class GraphNode(BaseModel):
    node_id: str
    name: str
    label: NodeLabel = Field(description="노드 유형 (NodeLabel)")
    domain: Domain | None = None
    source_file: str | None = None
    source_line: int | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    from_id: str
    to_id: str
    type: EdgeType = Field(description="엣지 유형 (EdgeType)")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="엣지 속성 (event: 화면 이벤트, via: 참조 컬럼, columns: DBIO 가 접근하는 컬럼 목록 등)"
    )


class StartNodeChoice(BaseModel):
    node_id: str
    matched_keyword: str
    reason: str


class ExplorerOutput(BaseModel):
    start_nodes: list[StartNodeChoice]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    hops_used: int = Field(ge=1, le=2)
    hop_extension_reason: str | None = Field(default=None, description="2hop 확장 시 근거")
    tool_call_count: int = 0
