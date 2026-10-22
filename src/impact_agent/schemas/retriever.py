"""Retriever 출력."""

from typing import Literal

from pydantic import BaseModel, Field

from .enums import Domain

SourceKind = Literal["source", "screen", "interface_spec", "common_code", "ops_doc"]


class Evidence(BaseModel):
    node_id: str | None = None
    file_path: str
    symbol: str | None = None
    location: str | None = Field(default=None, description="라인 범위/화면 ID 등")
    summary: str
    source_kind: SourceKind
    domain: Domain | None = None


class ReexploreRequest(BaseModel):
    node_ids: list[str]
    reason: str = Field(description="새로 발견된 연결과 재탐색 사유")


class RetrieverOutput(BaseModel):
    evidence: list[Evidence]
    nodes_without_evidence: list[str] = Field(default_factory=list)
    reexplore_request: list[ReexploreRequest] = Field(default_factory=list)
