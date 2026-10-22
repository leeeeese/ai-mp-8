"""Tool 입출력 모델."""

from typing import Literal

from pydantic import BaseModel, Field

from .. import config
from .enums import EdgeType, NodeLabel, SmPart, TraverseDirection
from .explorer import GraphEdge, GraphNode


# search_graph_nodes
class SearchGraphNodesInput(BaseModel):
    keyword: str
    sm_part: SmPart
    top_k: int = 5


class SearchGraphNodesItem(BaseModel):
    node_id: str
    name: str
    label: NodeLabel
    score: float


# expand_graph
class ExpandGraphInput(BaseModel):
    node_ids: list[str]
    hops: int = Field(default=1, ge=1, le=config.MAX_HOPS)
    direction: TraverseDirection = TraverseDirection.DOWNSTREAM
    rel_types: list[EdgeType] | None = None


class ExpandGraphOutput(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# get_node_detail
class GetNodeDetailInput(BaseModel):
    node_id: str


# read_source
class ReadSourceInput(BaseModel):
    node_ids: list[str] = Field(description="원문을 조회할 그래프 노드 ID")
    context_lines: int = Field(default=0, ge=0, description="노드 정의 위치 앞뒤로 더 읽을 줄 수")


class ReadSourceItem(BaseModel):
    node_id: str
    label: NodeLabel
    file_path: str
    start_line: int
    end_line: int | None = None
    content: str = Field(description="소스 원문 (그래프 노드의 source_file/source_line 기준 직접 조회)")


# save_report
class SaveReportInput(BaseModel):
    content: str
    format: Literal["md", "pdf"]


class SaveReportOutput(BaseModel):
    file_path: str
