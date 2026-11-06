"""Tool 입출력 모델."""

from typing import Literal

from pydantic import BaseModel, Field

from .. import config
from .enums import Domain, MatchSource, NodeRole, StopReason
from .explorer import GraphEdge, GraphNode
from .retriever import SourceKind


# search_graph_nodes
class SearchGraphNodesInput(BaseModel):
    terms: list[str] = Field(min_length=1, description="찾을 표현 (phrase + synonyms). 하나라도 맞으면 후보")
    domains: list[Domain] = Field(default_factory=list, description="비우면 전체 영역")
    labels: list[str] | None = Field(default=None, description="영역별 노드 유형 제한 (예: Screen, Column)")
    top_k: int = 5


class SearchGraphNodesItem(BaseModel):
    node_id: str
    name: str
    label: str
    role: NodeRole
    domain: Domain | None = None
    score: float
    matched_term: str | None = None
    match_source: MatchSource = MatchSource.TEXT


# traverse_graph
class TraverseGraphInput(BaseModel):
    start_ids: list[str] = Field(min_length=1)
    direction: Literal["forward", "reverse"]
    target_role: NodeRole
    hop_cap: int = Field(ge=1, le=config.MAX_HOPS_BIDIRECTIONAL)
    exclude_common: bool = True
    focus_ids: list[str] = Field(default_factory=list, description="Column 까지 갈 때 확장할 컬럼 (비우면 접근 컬럼 전체)")
    writes_only: bool = Field(default=False, description="역방향 Table → 데이터 접근에서 WRITES 만 따라감 (저장 진입점 찾기)")


class TraversePath(BaseModel):
    nodes: list[str] = Field(description="진입 → DB 순서")
    reached_role: NodeRole | None = None
    stop_reason: StopReason


class TraverseGraphOutput(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    paths: list[TraversePath]
    excluded_common: list[str] = Field(default_factory=list)
    frontier: list[str] = Field(default_factory=list, description="hop 상한에 걸린 노드")
    hops_used: int = 0


# get_node_detail
class GetNodeDetailInput(BaseModel):
    node_id: str


# read_source
class ReadSourceInput(BaseModel):
    node_ids: list[str] = Field(min_length=1)
    context_lines: int = Field(default=0, ge=0, description="노드 범위 앞뒤로 더 읽을 줄 수")


class ReadSourceItem(BaseModel):
    node_id: str
    source_kind: SourceKind
    file_path: str = Field(description="데이터 루트(data/) 기준 경로")
    start_line: int | None = None
    end_line: int | None = None
    content: str
    found: bool = True


# save_report
class SaveReportInput(BaseModel):
    content: str
    format: Literal["md", "pdf"]


class SaveReportOutput(BaseModel):
    file_path: str
