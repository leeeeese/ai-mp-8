"""Simulator 출력 (영역별 영향 경로)."""

from pydantic import BaseModel, Field

from .enums import Area, Confidence, Difficulty, Feasibility, ImpactType


class PathHop(BaseModel):
    """'노드 → 관계 → 영향 → 근거' 한 hop."""

    node: str
    relation: str
    impact: str
    evidence_ref: str | None = Field(default=None, description="Evidence 식별 (file_path#symbol)")
    confidence: Confidence


class ImpactPath(BaseModel):
    path_id: str
    hops: list[PathHop] = Field(description="A→C→X 순서")
    cross_domain: bool = Field(default=False, description="상품↔마케팅 도메인 간 연계 여부")


class Alternative(BaseModel):
    description: str
    feasibility: Feasibility
    difficulty: Difficulty
    pros_cons: str


class AreaImpact(BaseModel):
    area: Area
    impact_type: ImpactType
    paths: list[ImpactPath]
    feasibility: Feasibility
    difficulty: Difficulty
    difficulty_basis: str = Field(description="사전 확정한 난이도 판단 기준 중 적용한 항목")
    schedule_factors: list[str] = Field(default_factory=list)
    infeasible_reason: str | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    confidence: Confidence


class SimulationOutput(BaseModel):
    area_impacts: list[AreaImpact]
    requested_vs_alternative: str | None = Field(default=None, description="요청안/대안 비교 요약")
    unverified_segments: list[str] = Field(
        default_factory=list, description="확인하지 못한 구간·파일 (분석 한계로 전달)"
    )
