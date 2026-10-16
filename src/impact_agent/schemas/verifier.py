"""Verifier 출력."""

from typing import Literal

from pydantic import BaseModel, Field

from .enums import Area, VerifyResult

CheckKind = Literal["area_path_missing", "cross_domain_link", "redundant_path", "alternative_basis"]


class VerifyCheck(BaseModel):
    check: CheckKind
    area: Area | None = None
    result: VerifyResult
    note: str


class VerifierOutput(BaseModel):
    result: VerifyResult
    checks: list[VerifyCheck]
    reason: str | None = Field(default=None, description="보완 필요 시 필수")
    additional_scope: list[str] = Field(
        default_factory=list, description="추가로 볼 노드/영역/키워드 (보완 필요 시 필수)"
    )
