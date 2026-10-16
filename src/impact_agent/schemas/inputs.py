"""Supervisor 입력 / 후속 입력."""

from pydantic import BaseModel, Field

from .enums import AnalysisPoint, FollowupType, InputSource, InputType, SmPart


class UserInput(BaseModel):
    """Supervisor가 받는 최초 입력."""

    request_text: str
    sm_part: SmPart


class InputMeta(BaseModel):
    source: InputSource
    input_type: InputType
    analysis_point: AnalysisPoint


class FollowupInput(BaseModel):
    type: FollowupType
    content: str
    target_item_ids: list[str] = Field(default_factory=list, description="답변 대상 체크리스트 항목")
