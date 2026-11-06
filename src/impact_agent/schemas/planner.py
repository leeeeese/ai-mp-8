"""Planner 출력."""

from pydantic import BaseModel, Field

from .. import config
from .enums import AgentName, ChangeType, Complexity, Direction, Domain, MentionKind, NodeRole, RequestType


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
        default_factory=list, description="공통코드·상품 코드 매핑이 필요한 현업 용어 (현업 약칭, 상품 별칭 등)"
    )
    open_items: list[OpenItem] = Field(default_factory=list)


class Mention(BaseModel):
    """요건에서 "무엇을 말했는지". Planner 는 그래프를 모르므로 노드 ID 가 아니라 표현과 힌트만 넘긴다."""

    kind: MentionKind
    phrase: str = Field(description="요건에 나온 표현 그대로 (예: 캠페인 화면, 비마케팅 여부)")
    maybe_new: bool = Field(default=False, description="아직 시스템에 없을 수 있는 대상 (신규 항목 요청)")
    synonyms: list[str] = Field(default_factory=list, description="같은 뜻의 다른 표현 (현업·시스템 용어)")
    meaning: str | None = Field(default=None, description="의미 힌트 (예: 메시지가 광고 목적인지 구분하는 Y/N 값)")
    area: str | None = Field(default=None, description="업무 영역 힌트 (예: 캠페인, 권한)")
    confirmed_node: str | None = Field(default=None, description="사용자가 확정한 노드 ID. Supervisor 가 채우고 Planner 는 비워 둔다")


class ExploreItem(BaseModel):
    """Explorer 에 넘기는 탐색 명세 1건 (요건 항목 단위)."""

    item_id: int
    text: str = Field(description="요건 항목 문장")
    change_type: ChangeType
    mentions: list[Mention]
    target_role: NodeRole = Field(description="여기에 닿으면 멈춤 (config.CHANGE_TYPE_RULES)")
    direction: Direction
    hop_cap: int = Field(ge=1, le=config.MAX_HOPS_BIDIRECTIONAL, description="안전장치. 목표 판정이 우선")
    out_of_graph: list[str] = Field(default_factory=list, description="그래프 밖 영역 (예: 채널 전문). 탐색하지 않고 unresolved 로 넘김")


class ExcludeOption(BaseModel):
    is_common: bool = Field(default=True, description="공통 모듈·공통 SQL 을 탐색에서 제외 (제외 사실은 결과에 남김)")


class PlanStep(BaseModel):
    agent: AgentName
    purpose: str


class PlannerOutput(BaseModel):
    structured_request: StructuredRequest
    complexity: Complexity
    complexity_reason: str = Field(description="판정 근거 (필수)")
    domains: list[Domain] = Field(description="탐색 영역 (SM 선택 → config.SM_PART_DOMAINS)")
    items: list[ExploreItem] = Field(description="Explorer 탐색 명세")
    exclude: ExcludeOption = Field(default_factory=ExcludeOption)
    plan: list[PlanStep] = Field(
        description="simple: explorer, retriever / complex: explorer, retriever, simulator, verifier"
        " (KPI Agent·Composer 는 Supervisor 가 항상 실행)"
    )


class ReplanFeedback(BaseModel):
    """재계획마다 누적되는 항목 (replan_feedback)."""

    round: int
    reason: str
    additional_scope: list[str]
