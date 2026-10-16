"""Supervisor 입력 / 후속 입력."""

from typing import Literal

from pydantic import BaseModel, Field

from .enums import AnalysisPoint, FollowupType, InputSource, InputType, SmPart


class UserInput(BaseModel):
    """Supervisor가 받는 최초 입력."""

    request_text: str
    sm_part: SmPart
    output_options: list[Literal["mail_draft", "pdf"]] = Field(default_factory=list, description="추가 산출물 요청")


class InputMeta(BaseModel):
    source: InputSource
    input_type: InputType
    analysis_point: AnalysisPoint


class FollowupInput(BaseModel):
    type: FollowupType
    content: str
    target_item_ids: list[str] = Field(default_factory=list, description="답변 대상 체크리스트 항목")
