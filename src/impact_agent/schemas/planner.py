"""Planner 출력."""

from pydantic import BaseModel, Field

from .enums import AgentName, Complexity, RequestType


class OpenItem(BaseModel):
    """[업무 확정 필요] 항목 — 요건서에 없는 내용."""

    item: str
    reason: str = Field(description="왜 확정이 필요한지")


class StructuredRequest(BaseModel):
    target: str = Field(description="대상 (예: 신규 캠페인 화면)")
    action: str = Field(description="행위 (예: 비마케팅 여부 저장 후 채널 전송)")
    condition: str | None = None
    policy: str | None = None
    scope: str | None = Field(default=None, description="적용 범위")
    request_type: RequestType
    business_terms: list[str] = Field(
        default_factory=list, description="공통코드 매핑이 필요한 현업 용어 (예: 안보폰)"
    )
    open_items: list[OpenItem] = Field(default_factory=list)


class PlanStep(BaseModel):
    agent: AgentName
    purpose: str


class PlannerOutput(BaseModel):
    structured_request: StructuredRequest
    complexity: Complexity
    complexity_reason: str = Field(description="판정 근거 (필수)")
    plan: list[PlanStep] = Field(
        description="simple: explorer, retriever / complex: explorer, retriever, simulator, verifier"
    )


class ReplanFeedback(BaseModel):
    """재계획마다 누적되는 항목 (replan_feedback)."""

    round: int
    reason: str
    additional_scope: list[str]
