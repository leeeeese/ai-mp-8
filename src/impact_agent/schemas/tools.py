"""Tool 입출력 모델."""

from typing import Literal

from pydantic import BaseModel, Field

from .enums import Domain, SmPart
from .explorer import GraphEdge, GraphNode


# search_graph_nodes
class SearchGraphNodesInput(BaseModel):
    keyword: str
    sm_part: SmPart
    top_k: int = 5


class SearchGraphNodesItem(BaseModel):
    node_id: str
    name: str
    label: str
    score: float


# expand_graph
class ExpandGraphInput(BaseModel):
    node_ids: list[str]
    hops: int = Field(default=1, ge=1, le=2)
    rel_types: list[str] | None = None


class ExpandGraphOutput(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# get_node_detail
class GetNodeDetailInput(BaseModel):
    node_id: str


# search_source
class SearchSourceInput(BaseModel):
    query: str
    domain: Domain
    node_id: str | None = None
    top_k: int = 5


class SearchSourceItem(BaseModel):
    file_path: str
    symbol: str | None = None
    snippet: str
    score: float


# save_report
class SaveReportInput(BaseModel):
    content: str
    format: Literal["md", "pdf"]


class SaveReportOutput(BaseModel):
    file_path: str
