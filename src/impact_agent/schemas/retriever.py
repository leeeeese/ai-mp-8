"""Retriever 출력."""

from typing import Literal

from pydantic import BaseModel, Field

from .enums import Direction, Domain, NodeRole, ReexploreMode
from .react import ReactStep

SourceKind = Literal["source", "screen", "interface_spec", "common_code", "ops_doc"]


class Evidence(BaseModel):
    node_id: str | None = None
    file_path: str
    symbol: str | None = None
    location: str | None = Field(default=None, description="라인 범위 (예: L83-92)")
    summary: str
    source_kind: SourceKind
    domain: Domain | None = None
    content: str | None = Field(default=None, description="소스·문서 원문 (Simulator 가 판단에 사용)")


class ReexploreRequest(BaseModel):
    item_id: int | None = Field(default=None, description="대상 탐색 명세 항목")
    mode: ReexploreMode = ReexploreMode.CONTINUE
    node_ids: list[str] = Field(description="여기서부터 다시 탐색")
    target_role: NodeRole | None = Field(default=None, description="비우면 항목의 목표 역할")
    direction: Direction | None = Field(default=None, description="비우면 mode 기준 (reverse → reverse, 그 외 forward)")
    reason: str = Field(description="재탐색 사유")


class RetrieverOutput(BaseModel):
    evidence: list[Evidence]
    nodes_without_evidence: list[str] = Field(default_factory=list)
    reexplore_request: list[ReexploreRequest] = Field(default_factory=list)
    tool_call_count: int = 0
    react_trace: list[ReactStep] = Field(default_factory=list, description="원문 확인 뒤 ReAct 근거 보완 기록")
