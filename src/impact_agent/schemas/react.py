"""ReAct 단계 기록 (Explorer·Retriever). Thought → Action(tool) → Observation 을 한 단계로 남긴다."""

from typing import Any

from pydantic import BaseModel, Field


class ReactStep(BaseModel):
    step: int
    thought: str = Field(description="조회 결과를 근거로 한 판단")
    action: str = Field(description="호출한 tool 이름 또는 finish")
    args: dict[str, Any] = Field(default_factory=dict)
    observation: str = Field(description="tool 결과 요약 (finish 면 종료 사유)")
